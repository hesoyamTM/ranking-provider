from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Phase(str, Enum):
    """Текущая фаза диалога с пользователем."""

    EXTRACTING = "extracting"
    CLARIFYING = "clarifying"
    CONFIRMING = "confirming"
    RANKING = "ranking"
    SYNTHESIZING = "synthesizing"
    DONE = "done"


class ComponentSpec(BaseModel):
    """Спецификация одного компонента инфраструктуры.

    `specs` — словарь конкретных технических требований (vcpu, ram,
    engine, size_gb и т.п.). Это то место, куда LLM кладёт ответы юзера
    на уточняющие вопросы; иначе их некуда положить и `missing` никогда
    бы не закрывался.

    `missing` — список ключей обязательных полей, которые ExtractionAgent
    не смог извлечь; ClarificationAgent задаст вопросы строго по нему.

    `assumptions` — поля, которые были разумно дефолтнуты, чтобы не
    мучить пользователя лишними вопросами.
    """

    name: str
    clean_intent: str
    required_tags: list[str] = Field(default_factory=list)
    location_name: str = ""
    max_budget_rub: float | None = None
    specs: dict[str, str] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class SessionState(BaseModel):
    """Single source of truth для одного диалога.

    Хранится в `ChatRepository` и обновляется на каждом ходу диалога
    оркестратором.
    """

    phase: Phase = Phase.EXTRACTING
    components: list[ComponentSpec] = Field(default_factory=list)
    location_name: str = ""
    max_budget_total_rub: float | None = None
    rankings: dict[str, Any] | None = None
    phase_log: list[tuple[str, str]] = Field(default_factory=list)
    clarification_attempts: int = 0

    def transition(self, new_phase: Phase, reason: str = "") -> None:
        """Зафиксировать переход в новую фазу с причиной (для отладки)."""
        self.phase_log.append((self.phase.value, new_phase.value))
        self.phase = new_phase

    @property
    def total_missing(self) -> int:
        return sum(len(c.missing) for c in self.components)
