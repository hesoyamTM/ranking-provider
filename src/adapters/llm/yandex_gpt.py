from __future__ import annotations

import json
from typing import Any, AsyncGenerator, ClassVar

from openai import AsyncOpenAI

from src.models.agent import LLMResponse, ToolCall
from src.service.agent.protocols import (
    TOOL_ASK_CLARIFICATION as _TOOL_ASK_CLARIFICATION,
    TOOL_RANK_SERVICES as _TOOL_RANK_SERVICES,
)


class YandexGPTAdapter:
    """Адаптер LLM (YandexGPT через OpenAI-совместимый API).

    Хранит описание системного промпта и схемы всех доступных агенту
    инструментов (function calling).
    """

    SYSTEM_PROMPT: ClassVar[str] = (
        "Ты — аналитик данных в облачном провайдере. "
        "Твоя задача — собрать параметры запроса пользователя и вызвать "
        "функцию ранжирования услуг провайдера.\n\n"
        "ПРАВИЛА:\n"
        "1. clean_intent — краткая суть (например, 'VDS для VPN').\n"
        "2. required_tags — список технологий (['python', 'docker']).\n"
        "3. location_name — город или регион (пусто, если 'неважно').\n"
        "4. max_budget — число в РУБЛЯХ. Доллары конвертируй по курсу 100 руб/$. "
        "Если бюджет 'неважно/любой' — не передавай поле.\n\n"
        "ИНСТРУМЕНТЫ:\n"
        "- Если данных критически мало для ранжирования — вызови `ask_clarification`.\n"
        "- Если данных достаточно — вызови `rank_services`."
    )

    TOOL_ASK_CLARIFICATION: ClassVar[str] = _TOOL_ASK_CLARIFICATION
    TOOL_RANK_SERVICES: ClassVar[str] = _TOOL_RANK_SERVICES

    TOOLS: ClassVar[list[dict[str, Any]]] = [
        {
            "type": "function",
            "function": {
                "name": TOOL_ASK_CLARIFICATION,
                "description": (
                    "Задать пользователю уточняющий вопрос, если данных "
                    "недостаточно для ранжирования услуг."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Текст вежливого уточняющего вопроса.",
                        }
                    },
                    "required": ["question"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_RANK_SERVICES,
                "description": (
                    "Запустить ранжирование услуг провайдера по собранным "
                    "параметрам пользователя."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "clean_intent": {
                            "type": "string",
                            "description": "Краткая суть запроса.",
                        },
                        "required_tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Технологические теги.",
                        },
                        "location_name": {
                            "type": "string",
                            "description": "Город или регион (пусто, если неважно).",
                        },
                        "max_budget": {
                            "type": "number",
                            "description": "Бюджет в рублях.",
                        },
                    },
                    "required": ["clean_intent", "required_tags"],
                },
            },
        },
    ]

    def __init__(
        self, client: AsyncOpenAI, model: str, temperature: float = 0.1
    ) -> None:
        self._client = client
        self._model = model
        self._temperature = temperature

    @property
    def system_prompt(self) -> str:
        return self.SYSTEM_PROMPT

    async def chat(self, messages: list[dict[str, Any]]) -> LLMResponse:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=self.TOOLS,
            temperature=self._temperature,
        )
        message = response.choices[0].message

        tool_calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            try:
                arguments = (
                    json.loads(tc.function.arguments) if tc.function.arguments else {}
                )
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                ToolCall(id=tc.id, name=tc.function.name, arguments=arguments)
            )

        return LLMResponse(content=message.content, tool_calls=tool_calls)

    async def stream_chat(
        self, messages: list[dict[str, Any]]
    ) -> AsyncGenerator[str, None]:
        """Финальный стриминговый ответ без инструментов."""
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=self._temperature,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
