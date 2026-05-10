from __future__ import annotations

import uuid
from typing import Any, AsyncGenerator, Protocol, runtime_checkable

from src.models.agent import LLMResponse, Message, RankedResource
from src.models.service_package import UserQuery

TOOL_ASK_CLARIFICATION = "ask_clarification"
TOOL_RANK_SERVICES = "rank_services"


@runtime_checkable
class LLMClient(Protocol):
    """Клиент языковой модели с поддержкой function calling и стриминга."""

    @property
    def system_prompt(self) -> str: ...

    async def chat(self, messages: list[dict[str, Any]]) -> LLMResponse: ...

    def stream_chat(
        self, messages: list[dict[str, Any]]
    ) -> AsyncGenerator[str, None]: ...


@runtime_checkable
class Geocoder(Protocol):
    """Преобразование названия локации в координаты (lat, lon)."""

    async def geocode(self, region: str) -> tuple[float, float] | None: ...


@runtime_checkable
class RelevanceScorer(Protocol):
    def rank_marketplace_resources(
        self,
        query: UserQuery,
    ) -> list[RankedResource]: ...


@runtime_checkable
class ChatRepository(Protocol):
    """Хранилище истории сообщений чата."""

    async def create_chat(self, user_id: uuid.UUID) -> uuid.UUID:
        """Создать новый чат для существующего пользователя. Возвращает chat_id."""
        ...

    async def get_chats_by_user(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        """Вернуть список chat_id пользователя."""
        ...

    async def get_chat_by_id(
        self, chat_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Message]:
        """Вернуть историю сообщений чата (пустой список, если чата нет)."""
        ...

    async def append_message(
        self, chat_id: uuid.UUID, user_id: uuid.UUID, message: Message
    ) -> None:
        """Добавить одно сообщение в историю чата."""
        ...

    async def delete_chat(self, chat_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Удалить чат и все сообщения в нем."""
        ...
