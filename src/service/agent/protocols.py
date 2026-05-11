from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, AsyncGenerator, Protocol, runtime_checkable

from src.models.agent import LLMResponse, Message
from src.models.service_package import Provider, UserQuery
from src.models.session import SessionState
from src.service.scorer import ScoredService


class Intent(str, Enum):
    """Намерение пользователя относительно текущего диалога."""

    CONTINUE = "continue"
    RESTART = "restart"


@runtime_checkable
class LLMClient(Protocol):
    """Универсальный клиент LLM с function calling и стримингом."""

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
    ) -> LLMResponse: ...

    def stream(
        self,
        messages: list[Message],
        temperature: float | None = None,
    ) -> AsyncGenerator[str, None]: ...


@runtime_checkable
class Geocoder(Protocol):
    """Преобразование названия локации в координаты (lat, lon)."""

    async def geocode(self, region: str) -> tuple[float, float] | None: ...


@runtime_checkable
class RelevanceScorer(Protocol):
    async def rank_by_services(self, query: UserQuery) -> list[ScoredService]: ...

    async def rank_by_providers(self, query: UserQuery) -> list[ScoredService]: ...


@runtime_checkable
class ProviderRepository(Protocol):
    """Источник «белого списка» провайдеров для синтеза."""

    async def list_providers(self) -> list[Provider]: ...


@runtime_checkable
class ChatRepository(Protocol):
    """Хранилище истории сообщений и состояния сессии."""

    async def create_chat(self, user_id: uuid.UUID) -> uuid.UUID: ...

    async def get_chats_by_user(self, user_id: uuid.UUID) -> list[uuid.UUID]: ...

    async def get_chat_by_id(
        self, chat_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Message]: ...

    async def append_message(
        self, chat_id: uuid.UUID, user_id: uuid.UUID, message: Message
    ) -> None: ...

    async def delete_chat(self, chat_id: uuid.UUID, user_id: uuid.UUID) -> None: ...

    async def get_session_state(
        self, chat_id: uuid.UUID, user_id: uuid.UUID
    ) -> SessionState | None: ...

    async def save_session_state(
        self, chat_id: uuid.UUID, user_id: uuid.UUID, state: SessionState
    ) -> None: ...

    async def clear_session_state(
        self, chat_id: uuid.UUID, user_id: uuid.UUID
    ) -> None: ...
