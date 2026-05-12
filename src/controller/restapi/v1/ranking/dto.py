from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from src.models.artifact import ArtifactPayload


class ChatCreatedResponse(BaseModel):
    chat_id: uuid.UUID


class ChatListResponse(BaseModel):
    chats: list[uuid.UUID]


class ChatHistoryResponse(BaseModel):
    chat_id: uuid.UUID
    messages: list[dict[str, Any]]


class ArtifactResponse(BaseModel):
    id: uuid.UUID
    chat_id: uuid.UUID
    created_at: datetime
    payload: ArtifactPayload


class ArtifactSummary(BaseModel):
    id: uuid.UUID
    created_at: datetime
    title: str = ""
    provider_count: int = 0


class ArtifactListResponse(BaseModel):
    chat_id: uuid.UUID
    artifacts: list[ArtifactSummary]
