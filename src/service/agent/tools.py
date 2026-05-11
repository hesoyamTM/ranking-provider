from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from src.models.agent import Message, MessageToolCall, Role
from src.models.service_package import UserQuery
from src.service.agent.protocols import (
    Geocoder,
    LLMClient,
    ProviderRepository,
    RelevanceScorer,
)

logger = logging.getLogger(__name__)


class AgentToolExecutor:
    """Единое место объявления всех тулов агента и их выполнения."""

    # ── Имена инструментов ────────────────────────────────────────────────
    TOOL_GET_PROVIDERS = "get_providers"
    TOOL_RANK_BY_RAG = "rank_by_rag"

    _RANKING_PARAMS: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "clean_intent": {
                "type": "string",
                "description": "Развёрнутое описание потребности компонента для семантического поиска.",
            },
            "required_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Список обязательных технических или compliance-тегов (может быть пустым).",
            },
            "max_budget_rub": {
                "type": "number",
                "description": "Максимальный бюджет в рублях в месяц (опционально).",
            },
            "location_name": {
                "type": "string",
                "description": "Название региона/города для геопривязки (опционально).",
            },
        },
        "required": ["clean_intent"],
        "additionalProperties": False,
    }

    # ── Явные схемы инструментов (передаются в LLM) ───────────────────────
    TOOLS: ClassVar[list[dict[str, Any]]] = [
        {
            "type": "function",
            "function": {
                "name": TOOL_GET_PROVIDERS,
                "description": (
                    "Вернуть полный список провайдеров из репозитория PostgreSQL. "
                    "Вызови этот тул ровно один раз перед формированием финального ответа. "
                    "В итоговой суммаризации разрешено упоминать ТОЛЬКО провайдеров, "
                    "чьи provider_id/name присутствуют в результате этого тула."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_RANK_BY_RAG,
                "description": (
                    "Семантический RAG-подбор услуг по запросу компонента. "
                    "Возвращает готовый Markdown-текст с подходящими услугами "
                    "по провайдерам. Вызывай для каждого компонента системы."
                ),
                "parameters": _RANKING_PARAMS,
            },
        },
    ]

    def __init__(
        self,
        provider_repo: ProviderRepository,
        scorer: RelevanceScorer,
        geocoder: Geocoder,
    ) -> None:
        self._provider_repo = provider_repo
        self._scorer = scorer
        self._geocoder = geocoder

    # ── Диспетчер ─────────────────────────────────────────────────────────

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name == self.TOOL_GET_PROVIDERS:
            return await self._get_providers()
        if tool_name == self.TOOL_RANK_BY_RAG:
            return await self._rank(self.TOOL_RANK_BY_RAG, arguments)
        raise ValueError(f"AgentToolExecutor: неизвестный тул '{tool_name}'")

    # ── Реализации инструментов ───────────────────────────────────────────

    async def _get_providers(self) -> str:
        providers = await self._provider_repo.list_providers()
        return json.dumps(
            [
                {
                    "provider_id": p.provider_id,
                    "name": p.name,
                    "base_platform": p.base_platform,
                    "regions": p.regions,
                }
                for p in providers
            ],
            ensure_ascii=False,
        )

    async def _rank(self, tool_name: str, arguments: dict[str, Any]) -> str:
        query = await self._build_query(arguments)
        markdown = await self._scorer.rank_by_rag(user_query=query)
        logger.info("%s: markdown_len=%d", tool_name, len(markdown))
        return markdown

    async def _build_query(self, arguments: dict[str, Any]) -> UserQuery:
        location_name = arguments.get("location_name") or ""
        lat, lon = 0.0, 0.0
        if location_name:
            coords = await self._geocoder.geocode(location_name)
            if coords is not None:
                lat, lon = coords
        return UserQuery(
            clean_intent=arguments["clean_intent"],
            required_tags=arguments.get("required_tags") or [],
            max_budget=arguments.get("max_budget_rub"),
            location_name=location_name,
            target_lat=lat,
            target_lon=lon,
        )

    _STATUS: ClassVar[dict[str, str]] = {
        TOOL_GET_PROVIDERS: "Получаю список провайдеров...",
        TOOL_RANK_BY_RAG: "Ищу подходящие услуги...",
    }

    # ── Агентный цикл ─────────────────────────────────────────────────────

    async def run_tool_loop(
        self,
        llm: LLMClient,
        messages: list[Message],
        temperature: float = 0.1,
        max_rounds: int = 5,
    ) -> list[Message]:
        """Запустить цикл LLM → тул → LLM до тех пор, пока не останется
        вызовов тулов или не будет исчерпан лимит раундов.

        Возвращает полную историю сообщений (входные + ассистент + тул-ответы),
        готовую для передачи в финальный ``stream()``.
        """
        out: list[Message] = []
        async for _ in self.run_tool_loop_with_status(
            llm, messages, out, temperature=temperature, max_rounds=max_rounds
        ):
            pass
        return out

    async def run_tool_loop_with_status(
        self,
        llm: LLMClient,
        messages: list[Message],
        out_messages: list[Message],
        temperature: float = 0.1,
        max_rounds: int = 5,
    ) -> AsyncGenerator[str, None]:
        """Как run_tool_loop, но дополнительно стримит текстовые статусы.

        Статусы отдаются через ``yield``.
        Результирующие сообщения записываются в ``out_messages`` (мутация).
        """
        from typing import AsyncGenerator as _AG  # noqa: F401 – local import for type hint

        yield "Анализирую задачу..."
        current: list[Message] = list(messages)

        for round_num in range(1, max_rounds + 1):
            response = await llm.complete(
                current, tools=self.TOOLS, temperature=temperature
            )

            if not response.tool_calls:
                logger.info(
                    "AgentToolExecutor: тулы не вызваны на раунде %d", round_num
                )
                break

            logger.info(
                "AgentToolExecutor: раунд %d, вызванные тулы: %s",
                round_num,
                [tc.name for tc in response.tool_calls],
            )
            for tc in response.tool_calls:
                logger.info(
                    "AgentToolExecutor: tool_call name=%s args=%s",
                    tc.name,
                    json.dumps(tc.arguments, ensure_ascii=False, indent=2),
                )

            current.append(
                Message(
                    role=Role.ASSISTANT,
                    content=response.content or "",
                    tool_calls=[
                        MessageToolCall(
                            id=tc.id,
                            name=tc.name,
                            arguments=json.dumps(tc.arguments, ensure_ascii=False),
                        )
                        for tc in response.tool_calls
                    ],
                )
            )

            for tc in response.tool_calls:
                status = self._STATUS.get(tc.name, f"Вызываю {tc.name}...")
                intent = ""
                if isinstance(tc.arguments, dict):
                    intent = tc.arguments.get("clean_intent", "")
                if intent and tc.name != self.TOOL_GET_PROVIDERS:
                    # Укорачиваем до 40 символов для читаемости
                    short = intent if len(intent) <= 40 else intent[:37] + "..."
                    status = status.rstrip("...") + f" для «{short}»..."
                yield status

                try:
                    result = await self.execute(tc.name, tc.arguments)
                except ValueError as exc:
                    result = json.dumps({"error": str(exc)}, ensure_ascii=False)
                    logger.warning("AgentToolExecutor: %s", exc)

                current.append(
                    Message(
                        role=Role.TOOL,
                        content=result,
                        tool_call_id=tc.id,
                    )
                )
        else:
            logger.warning("AgentToolExecutor: превышен лимит раундов (%d)", max_rounds)

        yield "Формирую ответ..."
        out_messages.extend(current)
