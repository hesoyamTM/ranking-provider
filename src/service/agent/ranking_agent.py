from __future__ import annotations

import json
import logging
import uuid
from typing import Any, AsyncGenerator

from src.models.agent import Message, MessageToolCall, RankedResource, Role, ToolCall
from src.models.service_package import UserQuery
from src.service.agent.protocols import (
    TOOL_ASK_CLARIFICATION,
    TOOL_RANK_SERVICES,
    ChatRepository,
    Geocoder,
    LLMClient,
    RelevanceScorer,
)

logger = logging.getLogger(__name__)


class RankingAgent:
    """Агент с циклом вызова инструментов.

    История диалога хранится в ChatRepository и загружается по chat_id/user_id.
    При каждом вызове run пользовательское сообщение и итоговый ответ ассистента
    автоматически сохраняются в репозиторий.

    Пример использования:

        chat_id, user_id = await chat_repo.create_chat()

        # Шаг 1
        async for chunk in agent.run(chat_id, user_id, "Нужен VDS"):
            print(chunk)  # "Уточните: какой у вас бюджет?"

        # Шаг 2 — история подтягивается из репозитория автоматически
        async for chunk in agent.run(chat_id, user_id, "До 5000 рублей"):
            print(chunk)  # стриминг результата ранжирования
    """

    _MAX_ITERATIONS = 4

    def __init__(
        self,
        llm: LLMClient,
        geocoder: Geocoder,
        scorer: RelevanceScorer,
        chat_repo: ChatRepository,
    ) -> None:
        self._llm = llm
        self._geocoder = geocoder
        self._scorer = scorer
        self._chat_repo = chat_repo

    def send_message(
        self,
        chat_id: uuid.UUID,
        user_id: uuid.UUID,
        user_message: str,
    ) -> AsyncGenerator[str, None]:
        return self._stream_and_save(chat_id, user_id, user_message)

    async def _stream_and_save(
        self,
        chat_id: uuid.UUID,
        user_id: uuid.UUID,
        user_message: str,
    ) -> AsyncGenerator[str, None]:
        user_msg = Message(role=Role.USER, content=user_message)
        await self._chat_repo.append_message(chat_id, user_id, user_msg)

        history = await self._chat_repo.get_chat_by_id(chat_id, user_id)

        chunks: list[str] = []
        async for chunk in self._stream(history):
            chunks.append(chunk)
            yield chunk

        assistant_msg = Message(role=Role.ASSISTANT, content="".join(chunks))
        await self._chat_repo.append_message(chat_id, user_id, assistant_msg)

    async def _stream(self, history: list[Message]) -> AsyncGenerator[str, None]:
        messages: list[Message] = [
            Message(role=Role.SYSTEM, content=self._llm.system_prompt),
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
        return await self._scorer.rank_marketplace_resources(query=query)

    async def _build_query(self, arguments: dict[str, Any]) -> UserQuery:
        location = str(arguments.get("location_name") or "")
        target_lat, target_lon = 0.0, 0.0
        if location:
            coords = await self._geocoder.geocode(location)
            if coords is not None:
                target_lat, target_lon = coords

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
        return Message(
            role=Role.ASSISTANT,
            content=content or "",
            tool_calls=[
                MessageToolCall(
                    id=tc.id,
                    name=tc.name,
                    arguments=json.dumps(tc.arguments, ensure_ascii=False),
                )
                for tc in tool_calls
            ],
        )

    @staticmethod
    def _tool_result_message(tool_call_id: str, content: str) -> Message:
        return Message(
            role=Role.TOOL,
            content=content,
            tool_call_id=tool_call_id,
        )
