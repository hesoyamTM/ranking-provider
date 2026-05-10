from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

from src.models import Service
from src.models.service_package import UserQuery
from src.service.agent.protocols import (
    TOOL_ASK_CLARIFICATION,
    TOOL_RANK_SERVICES,
    Geocoder,
    LLMClient,
    Message,
    RankedResource,
    RelevanceScorer,
    ToolCall,
)

logger = logging.getLogger(__name__)


class RankingAgent:
    """Агент с циклом вызова инструментов.

    Принимает историю диалога и кандидатов для ранжирования.
    Возвращает async-генератор чанков ответа.

    Многошаговый диалог на стороне caller:

        history = [{"role": "user", "content": "Нужен VDS"}]

        # Шаг 1
        async for chunk in await agent.run(history, candidates):
            print(chunk)  # "Уточните: какой у вас бюджет?"

        # Пользователь ответил — caller добавляет оба сообщения в историю
        history.append({"role": "assistant", "content": question})
        history.append({"role": "user", "content": "До 5000 рублей"})

        # Шаг 2
        async for chunk in await agent.run(history, candidates):
            print(chunk)  # стриминг результата ранжирования
    """

    _MAX_ITERATIONS = 4

    def __init__(
        self,
        llm: LLMClient,
        geocoder: Geocoder,
        scorer: RelevanceScorer,
    ) -> None:
        self._llm = llm
        self._geocoder = geocoder
        self._scorer = scorer

    async def run(
        self,
        history: list[Message],
    ) -> AsyncGenerator[str, None]:
        return self._stream(history)

    async def _stream(self, history: list[Message]) -> AsyncGenerator[str, None]:
        messages: list[Message] = [
            {"role": "system", "content": self._llm.system_prompt},
            *history,
        ]

        for _ in range(self._MAX_ITERATIONS):
            response = await self._llm.chat(messages)

            if not response.tool_calls:
                yield response.content or ""
                return

            messages.append(
                self._assistant_message(response.content, response.tool_calls)
            )

            for tool_call in response.tool_calls:
                if tool_call.name == TOOL_ASK_CLARIFICATION:
                    yield str(tool_call.arguments.get("question", ""))
                    return

                if tool_call.name == TOOL_RANK_SERVICES:
                    ranked = await self._rank(tool_call.arguments)
                    messages.append(
                        self._tool_result_message(
                            tool_call.id, self._format_ranked(ranked)
                        )
                    )
                    async for chunk in self._llm.stream_chat(messages):
                        yield chunk
                    return

                logger.warning("Unknown tool call: %s", tool_call.name)

        yield "Не удалось завершить обработку запроса."

    async def _rank(
        self,
        arguments: dict[str, Any],
    ) -> list[RankedResource]:
        query = await self._build_query(arguments)
        return self._scorer.rank_marketplace_resources(query=query)

    async def _build_query(self, arguments: dict[str, Any]) -> UserQuery:
        location = str(arguments.get("location_name") or "")
        target_lat, target_lon = 0.0, 0.0
        if location:
            coords = await self._geocoder.get_coordinates(location)
            target_lat = float(coords.get("lat", 0.0))
            target_lon = float(coords.get("lon", 0.0))

        return UserQuery(
            clean_intent=str(arguments.get("clean_intent", "")),
            required_tags=list(arguments.get("required_tags") or []),
            max_budget=arguments.get("max_budget"),
            location_name=location,
            target_lat=target_lat,
            target_lon=target_lon,
        )

    @staticmethod
    def _format_ranked(resources: list[RankedResource]) -> str:
        if not resources:
            return "Подходящих услуг не найдено."
        lines = ["Ранжированный список услуг провайдера:"]
        for res in resources:
            svc = res.service
            lines.append(
                f"{res.rank}. {svc.name} ({svc.category}) — "
                f"от {svc.price_from_rub} руб. ({svc.pricing_model}). "
                f"{svc.description}"
            )
        return "\n".join(lines)

    @staticmethod
    def _assistant_message(
        content: str | None,
        tool_calls: list[ToolCall],
    ) -> Message:
        return {
            "role": "assistant",
            "content": content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ],
        }

    @staticmethod
    def _tool_result_message(tool_call_id: str, content: str) -> Message:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        }
