from __future__ import annotations

import logging
import uuid
from typing import AsyncGenerator

from src.models.agent import Message, Role
from src.models.artifact import Artifact
from src.models.session import Phase, SessionState
from src.service.agent.artifact_extractor import ArtifactExtractor
from src.service.agent.clarification_agent import ClarificationAgent
from src.service.agent.confirmation_agent import (
    ConfirmAction,
    ConfirmationAgent,
)
from src.service.agent.extraction_agent import ExtractionAgent
from src.service.agent.intent_classifier import IntentClassifier
from src.service.agent.protocols import ArtifactRepository, ChatRepository, Intent
from src.service.agent.synthesis_agent import SynthesisAgent

logger = logging.getLogger(__name__)

# Маркеры в стриме (на фронте отфильтровываются перед рендером markdown).
_STATUS_MARKER = "__STATUS__:"
_ARTIFACT_MARKER = "__ARTIFACT__:"
_SUGGESTIONS_MARKER = "__SUGGESTIONS__:"

# Префиксы строк, которые НЕ должны попадать в сохранённый текст ассистента —
# это транзиентные UI-маркеры, фронт их фильтрует на лету, но они не нужны
# в истории чата при перезагрузке.
_TRANSIENT_PREFIXES = (_STATUS_MARKER, _ARTIFACT_MARKER, _SUGGESTIONS_MARKER)


def _strip_transient_markers(text: str) -> str:
    """Удалить из текста строки-маркеры, оставив только содержательный ответ."""
    lines = [
        line for line in text.split("\n")
        if not line.lstrip().startswith(_TRANSIENT_PREFIXES)
    ]
    return "\n".join(lines).strip("\n")


def _build_artifact_title(state: SessionState, max_len: int = 60) -> str:
    """Краткое имя артефакта на основе компонентов и локации запроса."""
    parts = [c.name.strip() for c in state.components if c.name and c.name.strip()]
    base = " + ".join(parts) if parts else "Подбор провайдеров"
    if state.location_name:
        base = f"{base} · {state.location_name}"
    if len(base) > max_len:
        base = base[: max_len - 1].rstrip() + "…"
    return base


class AgentOrchestrator:
    """Корневой роутер: маршрутизирует ход диалога по фазам.

    Контракт совпадает с прежним RankingAgent: `send_message` возвращает
    async-генератор чанков ответа ассистента, а на фон сохраняет историю
    и обновлённый SessionState.
    """

    _MAX_CLARIFICATION_ATTEMPTS = 2

    def __init__(
        self,
        chat_repo: ChatRepository,
        intent: IntentClassifier,
        extraction: ExtractionAgent,
        clarification: ClarificationAgent,
        confirmation: ConfirmationAgent,
        synthesis: SynthesisAgent,
        artifact_extractor: ArtifactExtractor,
        artifact_repo: ArtifactRepository,
    ) -> None:
        self._chat_repo = chat_repo
        self._intent = intent
        self._extraction = extraction
        self._clarification = clarification
        self._confirmation = confirmation
        self._synthesis = synthesis
        self._artifact_extractor = artifact_extractor
        self._artifact_repo = artifact_repo

    def send_message(
        self,
        chat_id: uuid.UUID,
        user_id: uuid.UUID,
        user_message: str,
    ) -> AsyncGenerator[str, None]:
        return self._handle(chat_id, user_id, user_message)

    async def _handle(
        self,
        chat_id: uuid.UUID,
        user_id: uuid.UUID,
        user_message: str,
    ) -> AsyncGenerator[str, None]:
        logger.info(
            "Incoming user message: chat_id=%s user_id=%s len=%d preview=%r",
            chat_id, user_id, len(user_message), user_message[:200],
        )

        await self._chat_repo.append_message(
            chat_id, user_id, Message(role=Role.USER, content=user_message)
        )
        history = await self._chat_repo.get_chat_by_id(chat_id, user_id)

        state = await self._chat_repo.get_session_state(chat_id, user_id) or SessionState()
        logger.info(
            "Loaded state: phase=%s components=%d total_missing=%d",
            state.phase.value, len(state.components), state.total_missing,
        )

        # Intent: продолжить или сбросить.
        intent = await self._intent.classify(history, user_message)
        if intent is Intent.RESTART:
            await self._chat_repo.clear_session_state(chat_id, user_id)
            state = SessionState()
            logger.info("Phase RESET: state cleared by RESTART intent")

        chunks: list[str] = []
        async for chunk in self._route(state, user_message, history, chat_id, user_id):
            chunks.append(chunk)
            yield chunk

        await self._chat_repo.save_session_state(chat_id, user_id, state)
        clean_content = _strip_transient_markers("".join(chunks))
        await self._chat_repo.append_message(
            chat_id, user_id,
            Message(role=Role.ASSISTANT, content=clean_content),
        )
        logger.info(
            "Done: chat_id=%s phase_after=%s assistant_len=%d",
            chat_id, state.phase.value, sum(len(c) for c in chunks),
        )

    async def _route(
        self,
        state: SessionState,
        user_message: str,
        history: list[Message],
        chat_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> AsyncGenerator[str, None]:
        """Один проход по фазам. Мутирует поля `state` через setattr."""
        # CONFIRMING — особый случай: сначала парсим ответ юзера.
        if state.phase == Phase.CONFIRMING:
            decision = await self._confirmation.decide(state, user_message)
            if decision.action == ConfirmAction.CONFIRM:
                state.transition(Phase.RANKING, "user confirmed")
                async for chunk in self._run_ranking_and_synthesis(state, chat_id, user_id):
                    yield chunk
                return
            if decision.action == ConfirmAction.RESTART:
                await self._chat_repo.clear_session_state(chat_id, user_id)
                self._reset_state(state)
            else:
                state.transition(Phase.EXTRACTING, "user patch")
                user_message = decision.patch_text or user_message

        if state.phase in (Phase.EXTRACTING, Phase.CLARIFYING, Phase.DONE):
            was_clarifying = state.phase == Phase.CLARIFYING
            new_state = await self._extraction.update(state, user_message, history)
            self._copy_state(state, new_state)

            attempts = state.clarification_attempts
            if state.total_missing > 0 and attempts < self._MAX_CLARIFICATION_ATTEMPTS:
                state.clarification_attempts = attempts + 1 if was_clarifying else 1
                state.transition(Phase.CLARIFYING, "missing fields detected")
                logger.info(
                    "Clarifying attempt %d/%d (missing=%d)",
                    state.clarification_attempts,
                    self._MAX_CLARIFICATION_ATTEMPTS,
                    state.total_missing,
                )
                async for chunk in self._clarification.stream(state):
                    yield chunk
                return

            if state.total_missing > 0:
                logger.warning(
                    "Max clarification attempts reached — proceeding with incomplete spec (missing=%d)",
                    state.total_missing,
                )
            state.clarification_attempts = 0
            state.transition(Phase.CONFIRMING, "spec is complete")
            async for chunk in self._confirmation.render_spec(state):
                yield chunk
            return

        if state.phase == Phase.RANKING:
            async for chunk in self._run_ranking_and_synthesis(state, chat_id, user_id):
                yield chunk
            return

        if state.phase == Phase.SYNTHESIZING:
            async for chunk in self._run_ranking_and_synthesis(state, chat_id, user_id):
                yield chunk

    async def _run_ranking_and_synthesis(
        self,
        state: SessionState,
        chat_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> AsyncGenerator[str, None]:
        state.transition(Phase.SYNTHESIZING, "ranking via tools")
        markdown_parts: list[str] = []
        async for chunk in self._synthesis.stream(state):
            if not chunk.startswith(_STATUS_MARKER):
                markdown_parts.append(chunk)
            yield chunk
        state.transition(Phase.DONE, "synthesis finished")

        synthesis_markdown = "".join(markdown_parts).strip()
        if not synthesis_markdown:
            return

        try:
            payload = await self._artifact_extractor.extract(state, synthesis_markdown)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Artifact extraction failed: %s", exc)
            payload = None

        if payload is None or not payload.providers:
            logger.info("Artifact extraction produced empty payload — пропускаем сохранение")
            return

        if not payload.title:
            payload.title = _build_artifact_title(state)

        artifact = Artifact(chat_id=chat_id, user_id=user_id, payload=payload)
        try:
            await self._artifact_repo.save(artifact)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Artifact save failed: %s", exc)
            return

        logger.info(
            "Artifact saved: id=%s chat_id=%s providers=%d",
            artifact.id, chat_id, len(payload.providers),
        )
        yield f"\n{_ARTIFACT_MARKER}{artifact.id}\n"

    @staticmethod
    def _copy_state(target: SessionState, source: SessionState) -> None:
        """Скопировать все поля Pydantic-модели через setattr (без __dict__ хаков)."""
        for field in SessionState.model_fields:
            setattr(target, field, getattr(source, field))

    @staticmethod
    def _reset_state(state: SessionState) -> None:
        fresh = SessionState()
        for field in SessionState.model_fields:
            setattr(state, field, getattr(fresh, field))
