from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.models.service_package import Service

# Сообщение в формате OpenAI Chat API: {"role": ..., "content": ..., ...}
Message = dict[str, Any]


@dataclass
class ToolCall:
    """Вызов инструмента, выполненный моделью."""

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
