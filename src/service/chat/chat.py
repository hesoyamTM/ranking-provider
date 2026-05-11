from __future__ import annotations

import uuid
from typing import AsyncGenerator

from src.models.agent import Message
from src.service.agent.orchestrator import AgentOrchestrator
from src.service.agent.protocols import ChatRepository


class ChatService:
    """Сервис для работы с чатами и отправки сообщений агенту."""

    def __init__(self, chat_repo: ChatRepository, agent: AgentOrchestrator) -> None:
        self._chat_repo = chat_repo
        self._agent = agent

    async def create_chat(self, user_id: uuid.UUID) -> uuid.UUID:
        return await self._chat_repo.create_chat(user_id)

    async def get_chats(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        return await self._chat_repo.get_chats_by_user(user_id)

    async def get_history(
        self, chat_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Message] | None:
        """Вернуть историю сообщений чата или None, если чат не принадлежит пользователю."""
        chats = await self._chat_repo.get_chats_by_user(user_id)
        if chat_id not in chats:
            return None
        return await self._chat_repo.get_chat_by_id(chat_id, user_id)

    def send_message(
        self,
        chat_id: uuid.UUID,
        user_id: uuid.UUID,
        text: str,
    ) -> AsyncGenerator[str, None]:
        """Отправить сообщение агенту и вернуть стриминговый ответ."""
        return self._agent.send_message(chat_id, user_id, text)
