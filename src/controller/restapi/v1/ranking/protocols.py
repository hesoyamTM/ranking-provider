from typing import Protocol

from typing import AsyncGenerator
import uuid

from src.models.agent import Message


class ChatService(Protocol):
    async def create_chat(self, user_id: uuid.UUID) -> uuid.UUID: ...
    async def get_chats(self, user_id: uuid.UUID) -> list[uuid.UUID]: ...
    async def get_history(
        self, chat_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Message] | None: ...


class RankingAgent(Protocol):
    def send_message(
        self, chat_id: uuid.UUID, user_id: uuid.UUID, user_message: str
    ) -> AsyncGenerator[str, None]: ...
