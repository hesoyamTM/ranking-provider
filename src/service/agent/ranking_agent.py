from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncGenerator

from src.models.agent import Message, MessageToolCall, Role, ToolCall
from src.models.service_package import UserQuery
from src.service.agent._filter import validate_and_filter
from src.service.agent._formatter import format_for_llm, format_plan_for_llm
from src.service.agent.protocols import (
    TOOL_ASK_CLARIFICATION,
    TOOL_PLAN_SYSTEM,
    TOOL_RANK_SERVICES,
    ChatRepository,
    Geocoder,
    LLMClient,
    RelevanceScorer,
)
from src.service.scorer import ScoredService

logger = logging.getLogger(__name__)


class RankingAgent:
    """Агент с циклом вызова инструментов и валидацией выдачи.

    Логика:
      1. LLM собирает обогащённый контекст пользователя и вызывает
         `rank_services` (одиночный запрос) или `plan_system` (система
         из нескольких компонентов).
      2. Агент валидирует и фильтрует результаты скорера.
      3. При слабом качестве LLM получает hint и переформулирует запрос.
    """

    _MAX_ITERATIONS = 50
    _MAX_RANK_ATTEMPTS = 10

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
        logger.info(
            "Incoming user message: chat_id=%s user_id=%s len=%d preview=%r",
            chat_id, user_id, len(user_message), user_message[:200],
        )
        await self._chat_repo.append_message(
            chat_id, user_id, Message(role=Role.USER, content=user_message)
        )
        history = await self._chat_repo.get_chat_by_id(chat_id, user_id)
        logger.info("Loaded chat history: chat_id=%s messages=%d", chat_id, len(history))

        chunks: list[str] = []
        async for chunk in self._stream(history):
            chunks.append(chunk)
            yield chunk

        assistant_content = "".join(chunks)
        logger.info(
            "Agent response complete: chat_id=%s total_len=%d preview=%r",
            chat_id, len(assistant_content), assistant_content[:200],
        )
        await self._chat_repo.append_message(
            chat_id, user_id, Message(role=Role.ASSISTANT, content=assistant_content)
        )

    async def _stream(self, history: list[Message]) -> AsyncGenerator[str, None]:
        messages: list[Message] = [
            Message(role=Role.SYSTEM, content=self._llm.system_prompt),
            *history,
        ]
        rank_attempts = 0
        logger.info(
            "Starting agent loop: history=%d max_iterations=%d",
            len(history), self._MAX_ITERATIONS,
        )

        for iteration in range(1, self._MAX_ITERATIONS + 1):
            logger.info("Iteration %d/%d: %d messages", iteration, self._MAX_ITERATIONS, len(messages))
            response = await self._llm.chat(messages)
            logger.info(
                "LLM response: tool_calls=%d content_len=%d",
                len(response.tool_calls or []), len(response.content or ""),
            )

            if not response.tool_calls:
                yield response.content or ""
                return

            messages.append(self._assistant_message(response.content, response.tool_calls))

            should_retry = False
            for tool_call in response.tool_calls:
                logger.info("Tool call: %s (id=%s)", tool_call.name, tool_call.id)

                if tool_call.name == TOOL_ASK_CLARIFICATION:
                    yield str(tool_call.arguments.get("question", ""))
                    return

                if tool_call.name == TOOL_RANK_SERVICES:
                    rank_attempts += 1
                    logger.info(
                        "rank_services attempt %d/%d: intent=%r tags=%s",
                        rank_attempts, self._MAX_RANK_ATTEMPTS,
                        str(tool_call.arguments.get("clean_intent", ""))[:120],
                        tool_call.arguments.get("required_tags") or [],
                    )
                    ranked = await self._rank(tool_call.arguments)
                    filtered, quality = validate_and_filter(ranked, tool_call.arguments)
                    logger.info(
                        "Filter: kept=%d/%d top_score=%.4f needs_refinement=%s",
                        quality["candidates_kept"], quality["candidates_total"],
                        quality["top_final_score"], quality["needs_refinement"],
                    )
                    messages.append(
                        self._tool_result_message(
                            tool_call.id, format_for_llm(filtered, quality, tool_call.arguments)
                        )
                    )
                    if quality["needs_refinement"] and rank_attempts < self._MAX_RANK_ATTEMPTS:
                        logger.info("Weak quality — letting LLM refine: %r", quality.get("refinement_hint", ""))
                        should_retry = True
                        break
                    async for chunk in self._llm.stream_chat(messages):
                        yield chunk
                    return

                if tool_call.name == TOOL_PLAN_SYSTEM:
                    logger.info(
                        "plan_system: components=%d location=%r budget=%s",
                        len(tool_call.arguments.get("components") or []),
                        tool_call.arguments.get("location_name") or "",
                        tool_call.arguments.get("max_budget_total_rub"),
                    )
                    messages.append(
                        self._tool_result_message(
                            tool_call.id, await self._handle_plan(tool_call.arguments)
                        )
                    )
                    async for chunk in self._llm.stream_chat(messages):
                        yield chunk
                    return

                logger.warning("Unknown tool call: %s", tool_call.name)

            if not should_retry:
                break

        logger.warning("Agent loop exhausted: iterations=%d rank_attempts=%d", self._MAX_ITERATIONS, rank_attempts)
        yield "Не удалось завершить обработку запроса. Уточните, пожалуйста, требования."

    async def _rank(self, arguments: dict[str, Any]) -> list[ScoredService]:
        return await self._scorer.rank(query=await self._build_query(arguments))

    async def _build_query(self, arguments: dict[str, Any]) -> UserQuery:
        location = str(arguments.get("location_name") or "")
        target_lat, target_lon = 0.0, 0.0
        if location:
            coords = await self._geocoder.geocode(location)
            if coords is not None:
                target_lat, target_lon = coords
                logger.info("Geocoded %r -> %.6f, %.6f", location, target_lat, target_lon)
            else:
                logger.warning("Geocoder returned nothing for %r", location)
        return UserQuery(
            clean_intent=str(arguments.get("clean_intent", "")),
            required_tags=list(arguments.get("required_tags") or []),
            max_budget=arguments.get("max_budget"),
            location_name=location,
            target_lat=target_lat,
            target_lon=target_lon,
        )

    async def _handle_plan(self, arguments: dict[str, Any]) -> str:
        """Параллельно ранжирует все компоненты plan_system и возвращает payload для LLM."""
        components: list[dict[str, Any]] = arguments.get("components") or []
        location_name = str(arguments.get("location_name") or "")
        max_budget_total = arguments.get("max_budget_total_rub")
        budget_per_component = (
            max_budget_total / len(components) if max_budget_total and components else None
        )

        ranked_lists = await asyncio.gather(
            *[self._rank_component(c, location_name, budget_per_component) for c in components]
        )

        component_results: list[dict[str, Any]] = []
        for comp, ranked in zip(components, ranked_lists):
            filtered, quality = validate_and_filter(
                ranked,
                {"required_tags": comp.get("required_tags") or [], "max_budget": budget_per_component},
            )
            logger.info(
                "Plan component %r: kept=%d needs_refinement=%s top_score=%.4f",
                comp.get("name", ""), quality["candidates_kept"],
                quality["needs_refinement"], quality["top_final_score"],
            )
            component_results.append(
                {
                    "name": comp.get("name", ""),
                    "clean_intent": comp.get("clean_intent", ""),
                    "filtered": filtered,
                    "quality": quality,
                }
            )

        return format_plan_for_llm(component_results, arguments)

    async def _rank_component(
        self,
        component: dict[str, Any],
        location_name: str,
        max_budget: float | None,
    ) -> list[ScoredService]:
        args: dict[str, Any] = {
            "clean_intent": component.get("clean_intent", ""),
            "required_tags": component.get("required_tags") or [],
            "location_name": location_name,
        }
        if max_budget is not None:
            args["max_budget"] = max_budget
        return await self._rank(args)

    @staticmethod
    def _assistant_message(content: str | None, tool_calls: list[ToolCall]) -> Message:
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
        return Message(role=Role.TOOL, content=content, tool_call_id=tool_call_id)
