from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

from src.models.agent import Message, Role
from src.models.session import SessionState
from src.service.agent._taxonomy import hint_for, suggestions_per_field
from src.service.agent.prompts import load_prompt
from src.service.agent.protocols import LLMClient

logger = logging.getLogger(__name__)


class ClarificationAgent:
    """Стримит уточняющие вопросы по списку missing-полей."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._prompt = load_prompt("clarification")

    async def stream(self, state: SessionState) -> AsyncGenerator[str, None]:
        budget_missing = self._budget_missing(state)
        payload = self._build_payload(state, budget_missing)
        messages = [
            Message(role=Role.SYSTEM, content=self._prompt),
            Message(role=Role.USER, content=payload),
        ]
        logger.info(
            "Clarification: components=%d total_missing=%d",
            len(state.components), state.total_missing,
        )
        async for chunk in self._llm.stream(messages, temperature=0.3):
            yield chunk

        # Collect all missing fields across components for suggestions.
        # Бюджет — приоритетный вопрос: если он не задан, выносим его в начало списка.
        all_missing: list[str] = []
        if budget_missing:
            all_missing.append("budget")
        for c in state.components:
            for f in c.missing:
                if f not in all_missing:
                    all_missing.append(f)

        groups = suggestions_per_field(all_missing)
        if groups:
            yield f"\n__SUGGESTIONS__:{json.dumps(groups, ensure_ascii=False)}\n"

    @staticmethod
    def _budget_missing(state: SessionState) -> bool:
        if state.max_budget_total_rub is not None:
            return False
        # Если по каждому компоненту бюджет задан индивидуально — общий не нужен.
        if state.components and all(
            c.max_budget_rub is not None for c in state.components
        ):
            return False
        return True

    @staticmethod
    def _build_payload(state: SessionState, budget_missing: bool) -> str:
        components_view = []
        for c in state.components:
            components_view.append(
                {
                    "name": c.name,
                    "clean_intent": c.clean_intent,
                    "required_tags": c.required_tags,
                    "location_name": c.location_name,
                    "missing": c.missing,
                    "hints": {f: hint_for(f) for f in c.missing},
                }
            )
        return json.dumps(
            {
                "components": components_view,
                "global_location": state.location_name,
                "global_budget_rub": state.max_budget_total_rub,
                "budget_missing": budget_missing,
            },
            ensure_ascii=False,
            indent=2,
        )
