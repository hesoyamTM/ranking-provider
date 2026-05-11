from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncGenerator, ClassVar

from src.models.agent import Message, Role
from src.models.session import SessionState
from src.service.agent.prompts import load_prompt
from src.service.agent.protocols import LLMClient

logger = logging.getLogger(__name__)


class ConfirmAction(str, Enum):
    CONFIRM = "confirm"
    PATCH = "patch"
    RESTART = "restart"


@dataclass
class ConfirmDecision:
    action: ConfirmAction
    patch_text: str = ""


class ConfirmationAgent:
    """Подтверждение спецификации: рендерит карточку и парсит ответ юзера."""

    _TOOL_CONFIRM = "confirm"
    _TOOL_PATCH = "patch_spec"
    _TOOL_RESTART = "restart"

    _TOOLS: ClassVar[list[dict[str, Any]]] = [
        {
            "type": "function",
            "function": {
                "name": _TOOL_CONFIRM,
                "description": "Пользователь подтвердил спецификацию — запускаем подбор.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": _TOOL_PATCH,
                "description": (
                    "Пользователь хочет изменить спецификацию. "
                    "Передай его правку как короткое описание."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "updates": {
                            "type": "string",
                            "description": (
                                "Короткое описание правки на естественном "
                                "языке (например: 'поменять регион на Питер')."
                            ),
                        }
                    },
                    "required": ["updates"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": _TOOL_RESTART,
                "description": "Пользователь хочет начать заново.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._prompt = load_prompt("confirmation")

    async def render_spec(self, state: SessionState) -> AsyncGenerator[str, None]:
        payload = self._build_payload(state, mode="render")
        messages = [
            Message(role=Role.SYSTEM, content=self._prompt),
            Message(role=Role.USER, content=payload),
        ]
        logger.info("Confirmation render: components=%d", len(state.components))
        async for chunk in self._llm.stream(messages, temperature=0.2):
            yield chunk

    async def decide(
        self, state: SessionState, user_message: str
    ) -> ConfirmDecision:
        payload = self._build_payload(state, mode="decide", user_message=user_message)
        messages = [
            Message(role=Role.SYSTEM, content=self._prompt),
            Message(role=Role.USER, content=payload),
        ]

        response = await self._llm.complete(messages, tools=self._TOOLS, temperature=0.0)
        for tc in response.tool_calls:
            if tc.name == self._TOOL_CONFIRM:
                logger.info("Confirmation: CONFIRM")
                return ConfirmDecision(action=ConfirmAction.CONFIRM)
            if tc.name == self._TOOL_PATCH:
                updates = str(tc.arguments.get("updates", "")).strip()
                logger.info("Confirmation: PATCH (%r)", updates[:120])
                return ConfirmDecision(action=ConfirmAction.PATCH, patch_text=updates)
            if tc.name == self._TOOL_RESTART:
                logger.info("Confirmation: RESTART")
                return ConfirmDecision(action=ConfirmAction.RESTART)

        # Если LLM не выбрал тул — трактуем как PATCH с исходным текстом юзера.
        logger.warning("ConfirmationAgent: no tool call — treating as PATCH")
        return ConfirmDecision(action=ConfirmAction.PATCH, patch_text=user_message)

    @staticmethod
    def _build_payload(
        state: SessionState, mode: str, user_message: str = ""
    ) -> str:
        spec = {
            "components": [
                c.model_dump(exclude={"missing"}) for c in state.components
            ],
            "location_name": state.location_name,
            "max_budget_total_rub": state.max_budget_total_rub,
        }
        body: dict[str, Any] = {"mode": mode, "spec": spec}
        if user_message:
            body["user_message"] = user_message
        return json.dumps(body, ensure_ascii=False, indent=2)
