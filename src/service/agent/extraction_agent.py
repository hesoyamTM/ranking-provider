from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from src.models.agent import Message, Role
from src.models.session import ComponentSpec, SessionState
from src.service.agent._taxonomy import required_fields_for
from src.service.agent.prompts import load_prompt
from src.service.agent.protocols import LLMClient

logger = logging.getLogger(__name__)


class ExtractionAgent:
    """Извлекает/патчит спецификацию компонентов из реплик пользователя."""

    _TOOL_NAME = "upsert_components"

    _TOOLS: ClassVar[list[dict[str, Any]]] = [
        {
            "type": "function",
            "function": {
                "name": _TOOL_NAME,
                "description": (
                    "Полностью перезаписать список компонентов, регион и общий "
                    "бюджет на основе всей известной информации (старая спека "
                    "+ новая реплика пользователя)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "components": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "clean_intent": {"type": "string"},
                                    "required_tags": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "location_name": {"type": "string"},
                                    "max_budget_rub": {"type": "number"},
                                    "specs": {
                                        "type": "object",
                                        "description": (
                                            "Конкретные технические требования: "
                                            "vcpu, ram, engine, size_gb, "
                                            "expected_volume_gb, expected_traffic_tb, "
                                            "nodes_count, gpu_type, vram_gb. "
                                            "Значения — строки в свободной форме "
                                            "('4', '8 GB', 'PostgreSQL', '500')."
                                        ),
                                        "additionalProperties": {"type": "string"},
                                    },
                                    "assumptions": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": (
                                            "Поля, которые ты разумно дефолтнул "
                                            "вместо вопроса пользователю."
                                        ),
                                    },
                                },
                                "required": ["name", "clean_intent", "required_tags"],
                            },
                        },
                        "location_name": {
                            "type": "string",
                            "description": "Общий регион для всей системы.",
                        },
                        "max_budget_total_rub": {
                            "type": "number",
                            "description": "Общий бюджет ₽/мес на всю систему.",
                        },
                    },
                    "required": ["components"],
                },
            },
        }
    ]

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._prompt = load_prompt("extraction")

    async def update(
        self,
        state: SessionState,
        user_message: str,
        history: list[Message],
    ) -> SessionState:
        """Применить патч: вернуть новый SessionState с обновлёнными компонентами."""
        current_spec_json = self._serialize_spec(state)
        # Берём короткий хвост истории, чтобы LLM видел контекст уточнений.
        tail = [m for m in history[-10:] if m.role in (Role.USER, Role.ASSISTANT) and m.content]

        user_block = (
            f"### Текущая спецификация (current_spec):\n{current_spec_json}\n\n"
            f"### Последняя реплика пользователя:\n{user_message}"
        )
        messages: list[Message] = [
            Message(role=Role.SYSTEM, content=self._prompt),
            *tail,
            Message(role=Role.USER, content=user_block),
        ]

        response = await self._llm.complete(messages, tools=self._TOOLS, temperature=0.0)

        tool_call = next(
            (tc for tc in response.tool_calls if tc.name == self._TOOL_NAME), None
        )
        if tool_call is None:
            logger.warning(
                "ExtractionAgent: LLM не вернул %s. content=%r",
                self._TOOL_NAME, (response.content or "")[:200],
            )
            return state

        return self._apply(state, tool_call.arguments)

    @staticmethod
    def _serialize_spec(state: SessionState) -> str:
        if not state.components and not state.location_name and state.max_budget_total_rub is None:
            return "(пусто — это первый ход)"
        return json.dumps(
            {
                "components": [c.model_dump(exclude={"missing"}) for c in state.components],
                "location_name": state.location_name,
                "max_budget_total_rub": state.max_budget_total_rub,
            },
            ensure_ascii=False,
            indent=2,
        )

    @staticmethod
    def _apply(state: SessionState, args: dict[str, Any]) -> SessionState:
        raw_components = args.get("components") or []
        # Индекс старых компонентов по имени (case-insensitive) — нужен,
        # чтобы императивно домерджить specs/assumptions, если LLM забыл.
        old_by_name: dict[str, ComponentSpec] = {
            c.name.strip().lower(): c for c in state.components
        }
        components: list[ComponentSpec] = []
        for raw in raw_components:
            tags = [str(t) for t in (raw.get("required_tags") or [])]
            raw_specs = raw.get("specs") or {}
            new_specs = {str(k): str(v) for k, v in raw_specs.items() if v not in (None, "")}
            name = str(raw.get("name", "")).strip() or "Компонент"

            # Императивный merge: даже если LLM забыл вернуть старые specs,
            # мы их сохраним; новые перезаписывают старые по ключу.
            old = old_by_name.get(name.lower())
            merged_specs = dict(old.specs) if old else {}
            merged_specs.update(new_specs)

            assumptions = [str(a) for a in (raw.get("assumptions") or [])]
            if old and not assumptions:
                assumptions = list(old.assumptions)

            location = str(raw.get("location_name") or "")
            if not location and old:
                location = old.location_name

            budget = raw.get("max_budget_rub")
            if budget is None and old:
                budget = old.max_budget_rub

            spec = ComponentSpec(
                name=name,
                clean_intent=str(raw.get("clean_intent", "")).strip()
                             or (old.clean_intent if old else ""),
                required_tags=tags or (old.required_tags if old else []),
                location_name=location,
                max_budget_rub=budget,
                specs=merged_specs,
                assumptions=assumptions,
            )
            components.append(spec)

        new_state = state.model_copy(deep=True)
        new_state.components = components
        if "location_name" in args:
            new_state.location_name = str(args.get("location_name") or "")
        if "max_budget_total_rub" in args:
            new_state.max_budget_total_rub = args.get("max_budget_total_rub")

        # Пересчитываем missing с учётом глобального location_name.
        for spec in new_state.components:
            spec.missing = ExtractionAgent._compute_missing(spec, new_state.location_name)
        logger.info(
            "Extraction: components=%d total_missing=%d location=%r budget=%s",
            len(components), new_state.total_missing,
            new_state.location_name, new_state.max_budget_total_rub,
        )
        return new_state

    @staticmethod
    def _compute_missing(spec: ComponentSpec, global_location: str) -> list[str]:
        """Вычислить missing-поля по тегам, минус то, что уже задано."""
        required = required_fields_for(spec.required_tags)
        missing: list[str] = []
        for field in required:
            if field == "region" and (spec.location_name or global_location):
                continue
            if field in spec.specs:
                continue
            missing.append(field)
        return missing
