from __future__ import annotations

import uuid

from src.models.agent import Message


class InMemoryChatRepository:
    """In-memory реализация ChatRepository.

    Данные живут в памяти процесса и теряются при перезапуске.
    Пригодна для разработки и тестирования.
    """

    def __init__(self) -> None:
        # (user_id, chat_id) -> список сообщений
        self._store: dict[tuple[uuid.UUID, uuid.UUID], list[Message]] = {}

    async def create_chat(self, user_id: uuid.UUID) -> uuid.UUID:
        """Создать новый чат для пользователя. Возвращает chat_id."""
        chat_id = uuid.uuid4()
        self._store[(user_id, chat_id)] = []
        return chat_id

    async def get_chats_by_user(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        """Вернуть список chat_id пользователя."""
        return [chat_id for uid, chat_id in self._store if uid == user_id]

    async def get_chat_by_id(
        self, chat_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Message]:
        """Вернуть копию истории сообщений. Пустой список, если чат не найден."""
        return list(self._store.get((user_id, chat_id), []))

    async def append_message(
        self, chat_id: uuid.UUID, user_id: uuid.UUID, message: Message
    ) -> None:
        """Добавить сообщение в историю чата."""
        key = (user_id, chat_id)
        if key not in self._store:
            self._store[key] = []
        self._store[key].append(message)

    async def delete_chat(self, chat_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Удалить чат и все его сообщения."""
        self._store.pop((user_id, chat_id), None)
