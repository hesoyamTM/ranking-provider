from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _coerce_str(v: Any) -> Any:
    """LLM нередко возвращает null/число для строковых полей — нормализуем."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return str(v)


def _coerce_str_list(v: Any) -> Any:
    if v is None:
        return []
    if isinstance(v, list):
        return [_coerce_str(x) for x in v if x is not None]
    return [_coerce_str(v)]


class ArtifactService(BaseModel):
    """Одна услуга, рекомендованная провайдером в рамках компонента."""

    name: str
    category: str = ""
    description: str = ""
    monthly_price_rub: float | None = None
    price_from_rub: float | None = None
    price_unit: str = ""
    regions: list[str] = Field(default_factory=list)
    reason: str = ""

    @field_validator("name", "category", "description", "price_unit", "reason", mode="before")
    @classmethod
    def _str_coerce(cls, v: Any) -> Any:
        return _coerce_str(v)

    @field_validator("regions", mode="before")
    @classmethod
    def _regions_coerce(cls, v: Any) -> Any:
        return _coerce_str_list(v)


class ArtifactComponentServices(BaseModel):
    """Группа услуг по конкретному компоненту системы у одного провайдера."""

    component_name: str
    services: list[ArtifactService] = Field(default_factory=list)

    @field_validator("component_name", mode="before")
    @classmethod
    def _str_coerce(cls, v: Any) -> Any:
        return _coerce_str(v)


class ArtifactProvider(BaseModel):
    """Один провайдер в топе с услугами и объяснениями."""

    rank: int
    name: str
    why: str = ""
    components: list[ArtifactComponentServices] = Field(default_factory=list)
    total_monthly_price_rub: float | None = None

    @field_validator("name", "why", mode="before")
    @classmethod
    def _str_coerce(cls, v: Any) -> Any:
        return _coerce_str(v)


class ArtifactPayload(BaseModel):
    """Структурированное содержимое артефакта (без БД-полей)."""

    title: str = ""
    query_summary: str = ""
    components: list[str] = Field(default_factory=list)
    providers: list[ArtifactProvider] = Field(default_factory=list)
    final_recommendation: str = ""

    @field_validator("title", "query_summary", "final_recommendation", mode="before")
    @classmethod
    def _str_coerce(cls, v: Any) -> Any:
        return _coerce_str(v)

    @field_validator("components", mode="before")
    @classmethod
    def _components_coerce(cls, v: Any) -> Any:
        return _coerce_str_list(v)


class Artifact(BaseModel):
    """Артефакт ранжирования — финальный структурированный результат пайплайна."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    chat_id: uuid.UUID
    user_id: uuid.UUID
    payload: ArtifactPayload
    created_at: datetime = Field(default_factory=datetime.utcnow)
