from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel


class ChatCreatedResponse(BaseModel):
    chat_id: uuid.UUID


class ChatListResponse(BaseModel):
    chats: list[uuid.UUID]


class ChatHistoryResponse(BaseModel):
    chat_id: uuid.UUID
    messages: list[dict[str, Any]]
