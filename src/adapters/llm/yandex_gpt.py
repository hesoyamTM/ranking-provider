from __future__ import annotations

import json
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI

from src.models.agent import LLMResponse, Message, ToolCall


class YandexGPTAdapter:
    """Универсальный LLM-адаптер (YandexGPT через OpenAI-совместимый API).

    Без встроенного system_prompt и без встроенных tools — каждый
    суб-агент сам передаёт свой промпт первым сообщением и свои тулы.
    """

    _DEFAULT_TEMPERATURE = 0.1

    def __init__(
        self, client: AsyncOpenAI, model: str, temperature: float = _DEFAULT_TEMPERATURE
    ) -> None:
        self._client = client
        self._model = model
        self._default_temperature = temperature

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Один-шот вызов: возвращает либо текст, либо tool_calls."""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [m.to_openai_dict() for m in messages],
            "temperature": temperature if temperature is not None else self._default_temperature,
        }
        if tools:
            kwargs["tools"] = tools

        response = await self._client.chat.completions.create(**kwargs)
        message = response.choices[0].message

        tool_calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            try:
                arguments = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                ToolCall(id=tc.id, name=tc.function.name, arguments=arguments)
            )

        return LLMResponse(content=message.content, tool_calls=tool_calls)

    async def stream(
        self,
        messages: list[Message],
        temperature: float | None = None,
    ) -> AsyncGenerator[str, None]:
        """Стриминговый текстовый ответ без тулов."""
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=[m.to_openai_dict() for m in messages],
            temperature=temperature if temperature is not None else self._default_temperature,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
