from dataclasses import dataclass
from decimal import Decimal
from pydantic import BaseModel, Field

from src.models import Service

@dataclass(frozen=True, slots=True)
class Score:
    semantic: float
    tags: float
    final_score: float

@dataclass(frozen=True, slots=True)
class ScoredService:
    service: Service
    score: Score

@dataclass(frozen=True, slots=True)
class ServiceDTO(BaseModel):
    provider_id: str
    service_id: str
    category: str
    name: str
    description: str
    pricing_model: str
    price_from_rub: Decimal
    price_unit: str
    score: float
    compliance_tags: list[str] = Field(default_factory=list)
    tech_tags: list[str] = Field(default_factory=list)