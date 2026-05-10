from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.models.service_package import Service


class Role(str, Enum):
    """Роль участника диалога (формат OpenAI Chat API)."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class MessageToolCall:
    """Вызов инструмента, прикреплённый к сообщению ассистента в истории.

    В отличие от ``ToolCall`` (см. ниже), здесь ``arguments`` — это уже
    сериализованная JSON-строка, так как именно в таком виде ассистентский
    tool_call хранится в истории и принимается OpenAI-совместимым API.
    """

    id: str
    name: str
    arguments: str


@dataclass
class Message:
    """Сообщение в истории диалога (формат OpenAI Chat API).

    Поддерживаемые варианты:
      * system   — ``role=SYSTEM``, ``content=<текст>``.
      * user     — ``role=USER``, ``content=<текст>``.
      * assistant — ``role=ASSISTANT``, ``content=<текст>``, опционально
        ``tool_calls=[...]`` если модель решила вызвать инструменты.
      * tool     — ``role=TOOL``, ``content=<результат>``, обязательное
        ``tool_call_id`` (id того tool_call, ответ на который мы передаём).
    """

    role: Role
    content: str = ""
    tool_calls: list[MessageToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)

    def to_openai_dict(self) -> dict[str, Any]:
        """Преобразовать сообщение в dict, ожидаемый OpenAI-совместимым API.

        Поле ``id`` — внутренний идентификатор и в OpenAI-формат не включается.
        """
        data: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.tool_calls:
            data["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id is not None:
            data["tool_call_id"] = self.tool_call_id
        return data


@dataclass
class ToolCall:
    """Вызов инструмента, выполненный моделью (распарсенный ответ LLM)."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Ответ модели на один шаг диалога."""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ScoringWeights:
    semantic: float = 0.4
    price: float = 0.3
    geo: float = 0.2
    tags: float = 0.1


@dataclass
class RankedResource:
    service: Service
    score: float
    rank: int
