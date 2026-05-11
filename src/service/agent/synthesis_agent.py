from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

from src.models.agent import Message, Role
from src.models.session import SessionState
from src.service.agent.prompts import load_prompt
from src.service.agent.protocols import LLMClient

logger = logging.getLogger(__name__)


class SynthesisAgent:
    """LLM-синтез финального ответа через tool loop.

    ЛММ сама вызывает rank_by_rag для каждого компонента,
    затем стримит финальный markdown на основе результатов.
    """

    def __init__(self, llm: LLMClient, tool_executor: "AgentToolExecutor") -> None:  # noqa: F821
        self._llm = llm
        self._tools = tool_executor
        self._prompt = load_prompt("synthesis")

    async def stream(self, state: SessionState) -> AsyncGenerator[str, None]:
        if not state.components:
            logger.warning("SynthesisAgent: нет компонентов в state")
            yield "Не удалось получить данные для подбора. Попробуй уточнить запрос."
            return

        payload = json.dumps(
            {
                "components": [
                    {
                        "name": c.name,
                        "clean_intent": c.clean_intent,
                        "required_tags": list(c.required_tags),
                        "max_budget_rub": c.max_budget_rub,
                        "location_name": c.location_name,
                    }
                    for c in state.components
                ],
                "location_name": state.location_name,
                "max_budget_total_rub": state.max_budget_total_rub,
            },
            ensure_ascii=False,
            indent=2,
        )

        messages: list[Message] = [
            Message(role=Role.SYSTEM, content=self._prompt),
            Message(role=Role.USER, content=payload),
        ]

        # Даём достаточно раундов: 1 тул × N компонентов + 1 на get_providers
        max_rounds = max(6, len(state.components) + 2)

        logger.info(
            "SynthesisAgent: запуск tool loop, components=%d max_rounds=%d",
            len(state.components),
            max_rounds,
        )

        out_messages: list[Message] = []
        async for status in self._tools.run_tool_loop_with_status(
            self._llm, messages, out_messages, temperature=0.1, max_rounds=max_rounds
        ):
            yield f"__STATUS__:{status}\n"

        logger.info("SynthesisAgent: tool loop завершён, стримим синтез")

        async for chunk in self._llm.stream(out_messages, temperature=0.4):
            yield chunk
