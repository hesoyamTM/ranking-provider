from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.controller.restapi.v1.ranking.dto import (
    ChatCreatedResponse,
    ChatHistoryResponse,
    ChatListResponse,
)
from src.controller.restapi.v1.ranking.protocols import ChatService, RankingAgent

# Фиксированное пространство имён для генерации user_id через uuid5
_USER_ID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

_COOKIE_NAME = "user_id"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 год


class SendMessageRequest(BaseModel):
    text: str


def _resolve_user_id(request: Request, response: Response) -> uuid.UUID:
    """Вернуть user_id из cookie или создать новый по отпечатку браузера.

    Новый id генерируется через uuid5 от User-Agent, что даёт стабильный
    идентификатор для одного браузера между сессиями.
    При отсутствии User-Agent используется uuid4.
    """
    raw = request.cookies.get(_COOKIE_NAME)
    if raw:
        try:
            return uuid.UUID(raw)
        except ValueError:
            pass

    user_agent = request.headers.get("user-agent", "")
    if user_agent:
        user_id = uuid.uuid5(_USER_ID_NAMESPACE, user_agent)
    else:
        user_id = uuid.uuid4()

    response.set_cookie(
        key=_COOKIE_NAME,
        value=str(user_id),
        httponly=True,
        samesite="lax",
        max_age=_COOKIE_MAX_AGE,
    )
    return user_id


def get_router(chat_service: ChatService, agent: RankingAgent) -> APIRouter:
    router = APIRouter(prefix="/chats", tags=["chats"])

    @router.post("", response_model=ChatCreatedResponse, status_code=201)
    async def create_chat(request: Request, response: Response) -> ChatCreatedResponse:
        """Создать новый чат для текущего пользователя."""
        user_id = _resolve_user_id(request, response)
        chat_id = await chat_service.create_chat(user_id)
        return ChatCreatedResponse(chat_id=chat_id)

    @router.get("", response_model=ChatListResponse)
    async def list_chats(request: Request, response: Response) -> ChatListResponse:
        """Получить список чатов текущего пользователя."""
        user_id = _resolve_user_id(request, response)
        chats = await chat_service.get_chats(user_id)
        return ChatListResponse(chats=chats)

    @router.get("/{chat_id}", response_model=ChatHistoryResponse)
    async def get_chat(
        chat_id: uuid.UUID,
        request: Request,
        response: Response,
    ) -> ChatHistoryResponse:
        """Получить историю сообщений чата."""
        user_id = _resolve_user_id(request, response)
        history = await chat_service.get_history(chat_id, user_id)
        if history is None:
            raise HTTPException(status_code=404, detail="Chat not found")
        return ChatHistoryResponse(chat_id=chat_id, messages=history)

    @router.post("/{chat_id}/messages")
    async def send_message(
        chat_id: uuid.UUID,
        body: SendMessageRequest,
        request: Request,
        response: Response,
    ) -> StreamingResponse:
        """Отправить сообщение в чат и получить стриминговый ответ агента."""
        user_id = _resolve_user_id(request, response)

        history = await chat_service.get_history(chat_id, user_id)
        if history is None:
            raise HTTPException(status_code=404, detail="Chat not found")

        stream = agent.send_message(chat_id, user_id, body.text)
        return StreamingResponse(stream, media_type="text/plain")  # type: ignore

    return router
