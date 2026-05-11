from __future__ import annotations

import logging
from typing import Any, ClassVar

from src.models.agent import Message, Role
from src.service.agent.prompts import load_prompt
from src.service.agent.protocols import Intent, LLMClient

logger = logging.getLogger(__name__)


class IntentClassifier:
    """LLM-классификатор намерения: продолжить или начать заново."""

    _TOOLS: ClassVar[list[dict[str, Any]]] = [
        {
            "type": "function",
            "function": {
                "name": "intent_continue",
                "description": "Пользователь продолжает текущий сценарий.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "intent_restart",
                "description": "Пользователь хочет сбросить контекст и начать заново.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._prompt = load_prompt("intent")

    async def classify(
        self, history: list[Message], user_message: str
    ) -> Intent:
        # Достаточно последних 4 сообщений для контекста — экономим токены.
        tail = history[-4:] if history else []
        context_lines = [f"{m.role.value}: {m.content[:200]}" for m in tail if m.content]
        context_block = "\n".join(context_lines) if context_lines else "(чат только начался)"

        messages = [
            Message(role=Role.SYSTEM, content=self._prompt),
            Message(
                role=Role.USER,
                content=(
                    f"### Краткий контекст диалога:\n{context_block}\n\n"
                    f"### Последняя реплика пользователя:\n{user_message}"
                ),
            ),
        ]

        response = await self._llm.complete(messages, tools=self._TOOLS, temperature=0.0)

        for tc in response.tool_calls:
            if tc.name == "intent_restart":
                logger.info("Intent classified: RESTART")
                return Intent.RESTART
            if tc.name == "intent_continue":
                logger.info("Intent classified: CONTINUE")
                return Intent.CONTINUE

        logger.warning("Intent classifier returned no tool call — defaulting to CONTINUE")
        return Intent.CONTINUE
