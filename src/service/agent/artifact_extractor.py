from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from src.models.agent import Message, Role
from src.models.artifact import ArtifactPayload
from src.models.session import SessionState
from src.service.agent.prompts import load_prompt
from src.service.agent.protocols import LLMClient

logger = logging.getLogger(__name__)


class ArtifactExtractor:
    """Превращает финальный markdown синтеза в структурированный артефакт."""

    _TOOL_NAME = "save_artifact"

    _SERVICE_SCHEMA: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "category": {"type": "string"},
            "description": {"type": "string"},
            "monthly_price_rub": {"type": ["number", "null"]},
            "price_from_rub": {"type": ["number", "null"]},
            "price_unit": {"type": "string"},
            "regions": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
        },
        "required": ["name"],
    }

    _COMPONENT_SCHEMA: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "component_name": {"type": "string"},
            "services": {"type": "array", "items": _SERVICE_SCHEMA},
        },
        "required": ["component_name", "services"],
    }

    _PROVIDER_SCHEMA: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "rank": {"type": "integer"},
            "name": {"type": "string"},
            "why": {"type": "string"},
            "components": {"type": "array", "items": _COMPONENT_SCHEMA},
            "total_monthly_price_rub": {"type": ["number", "null"]},
        },
        "required": ["rank", "name"],
    }

    _TOOLS: ClassVar[list[dict[str, Any]]] = [
        {
            "type": "function",
            "function": {
                "name": _TOOL_NAME,
                "description": "Сохранить структурированный артефакт ранжирования провайдеров.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query_summary": {"type": "string"},
                        "components": {"type": "array", "items": {"type": "string"}},
                        "providers": {"type": "array", "items": _PROVIDER_SCHEMA},
                        "final_recommendation": {"type": "string"},
                    },
                    "required": ["providers"],
                },
            },
        }
    ]

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._prompt = load_prompt("artifact_extraction")

    async def extract(
        self, state: SessionState, synthesis_markdown: str
    ) -> ArtifactPayload | None:
        components_json = json.dumps(
            {
                "components": [
                    {"name": c.name, "clean_intent": c.clean_intent}
                    for c in state.components
                ],
            },
            ensure_ascii=False,
        )

        user_block = (
            f"### Components (JSON):\n{components_json}\n\n"
            f"### Synthesis markdown:\n{synthesis_markdown}"
        )

        messages: list[Message] = [
            Message(role=Role.SYSTEM, content=self._prompt),
            Message(role=Role.USER, content=user_block),
        ]

        tool_call = await self._call_llm(messages, force=True)
        if tool_call is None:
            # Некоторые провайдеры (включая Yandex GPT) могут не поддерживать
            # форсированный tool_choice — пробуем ещё раз без него.
            logger.info(
                "ArtifactExtractor: повтор без tool_choice (форсированный вызов не сработал)"
            )
            tool_call = await self._call_llm(messages, force=False)

        if tool_call is None:
            return None

        args = tool_call.arguments
        logger.info(
            "ArtifactExtractor: получен tool_call %s, providers=%d",
            self._TOOL_NAME,
            len(args.get("providers") or []),
        )
        try:
            return ArtifactPayload.model_validate(args)
        except Exception as exc:
            logger.warning(
                "ArtifactExtractor: payload не прошёл валидацию целиком (%s) — "
                "пробую частично: отбрасываю невалидных провайдеров.",
                exc,
            )

        # Fallback: валидируем поэлементно, выкидывая поломанные блоки.
        return self._validate_partial(args)

    @staticmethod
    def _validate_partial(args: dict[str, Any]) -> ArtifactPayload | None:
        from src.models.artifact import ArtifactProvider

        good_providers = []
        for raw in args.get("providers") or []:
            try:
                good_providers.append(ArtifactProvider.model_validate(raw))
            except Exception as exc:
                logger.warning(
                    "ArtifactExtractor: пропускаю провайдера %r — %s",
                    (raw or {}).get("name"),
                    exc,
                )
        if not good_providers:
            logger.warning("ArtifactExtractor: после частичной валидации провайдеров не осталось")
            return None
        return ArtifactPayload(
            query_summary=args.get("query_summary") or "",
            components=[c for c in (args.get("components") or []) if c is not None],
            providers=good_providers,
            final_recommendation=args.get("final_recommendation") or "",
        )

    async def _call_llm(self, messages: list[Message], force: bool):
        kwargs: dict[str, Any] = {"tools": self._TOOLS, "temperature": 0.0}
        if force:
            kwargs["tool_choice"] = {
                "type": "function",
                "function": {"name": self._TOOL_NAME},
            }
        try:
            response = await self._llm.complete(messages, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ArtifactExtractor: LLM call failed (force=%s): %s", force, exc
            )
            return None

        tool_call = next(
            (tc for tc in response.tool_calls if tc.name == self._TOOL_NAME), None
        )
        if tool_call is None:
            logger.warning(
                "ArtifactExtractor: LLM не вернул %s (force=%s). content=%r tool_calls=%r",
                self._TOOL_NAME,
                force,
                (response.content or "")[:200],
                [tc.name for tc in response.tool_calls],
            )
        return tool_call
