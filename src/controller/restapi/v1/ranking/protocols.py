from typing import Protocol

from typing import AsyncGenerator
import uuid

from src.models.agent import Message
from src.models.artifact import Artifact


class ChatService(Protocol):
    async def create_chat(self, user_id: uuid.UUID) -> uuid.UUID: ...
    async def get_chats(self, user_id: uuid.UUID) -> list[uuid.UUID]: ...
    async def get_history(
        self, chat_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Message] | None: ...
    async def get_artifact(
        self, artifact_id: uuid.UUID, user_id: uuid.UUID
    ) -> Artifact | None: ...
    async def get_latest_artifact(
        self, chat_id: uuid.UUID, user_id: uuid.UUID
    ) -> Artifact | None: ...
    async def list_artifacts(
        self, chat_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Artifact]: ...


class RankingAgent(Protocol):
    def send_message(
        self, chat_id: uuid.UUID, user_id: uuid.UUID, user_message: str
    ) -> AsyncGenerator[str, None]: ...
