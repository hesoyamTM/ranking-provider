from __future__ import annotations

import uuid

from src.models.agent import Message
from src.models.session import SessionState


class InMemoryChatRepository:
    """In-memory реализация ChatRepository.

    Хранит историю сообщений и SessionState в памяти процесса.
    Для multi-instance/persistence потребуется отдельная реализация.
    """

    def __init__(self) -> None:
        self._messages: dict[tuple[uuid.UUID, uuid.UUID], list[Message]] = {}
        self._states: dict[tuple[uuid.UUID, uuid.UUID], SessionState] = {}

    async def create_chat(self, user_id: uuid.UUID) -> uuid.UUID:
        chat_id = uuid.uuid4()
        self._messages[(user_id, chat_id)] = []
        return chat_id

    async def get_chats_by_user(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        return [chat_id for uid, chat_id in self._messages if uid == user_id]

    async def get_chat_by_id(
        self, chat_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Message]:
        return list(self._messages.get((user_id, chat_id), []))

    async def append_message(
        self, chat_id: uuid.UUID, user_id: uuid.UUID, message: Message
    ) -> None:
        key = (user_id, chat_id)
        if key not in self._messages:
            self._messages[key] = []
        self._messages[key].append(message)

    async def delete_chat(self, chat_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self._messages.pop((user_id, chat_id), None)
        self._states.pop((user_id, chat_id), None)

    async def get_session_state(
        self, chat_id: uuid.UUID, user_id: uuid.UUID
    ) -> SessionState | None:
        state = self._states.get((user_id, chat_id))
        return state.model_copy(deep=True) if state is not None else None

    async def save_session_state(
        self, chat_id: uuid.UUID, user_id: uuid.UUID, state: SessionState
    ) -> None:
        self._states[(user_id, chat_id)] = state.model_copy(deep=True)

    async def clear_session_state(
        self, chat_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        self._states.pop((user_id, chat_id), None)
