from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Protocol, runtime_checkable

from src.models import Service
from src.models.service_package import UserQuery

TOOL_ASK_CLARIFICATION = "ask_clarification"
TOOL_RANK_SERVICES = "rank_services"

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


@runtime_checkable
class LLMClient(Protocol):
    """Клиент языковой модели с поддержкой function calling и стриминга."""

    system_prompt: str

    async def chat(self, messages: list[dict[str, Any]]) -> LLMResponse: ...

    def stream_chat(
        self, messages: list[dict[str, Any]]
    ) -> AsyncGenerator[str, None]: ...


@runtime_checkable
class Geocoder(Protocol):
    """Преобразование названия локации в координаты."""

    async def get_coordinates(self, location_name: str) -> dict[str, float]: ...


@runtime_checkable
class RelevanceScorer(Protocol):
    def rank_marketplace_resources(
        self,
        query: UserQuery,
    ) -> list[RankedResource]: ...
