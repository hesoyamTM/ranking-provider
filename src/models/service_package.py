from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class Provider(BaseModel):
    provider_id: str
    name: str
    base_platform: str
    regions: list[str] = Field(default_factory=list)


class Service(BaseModel):
    service_id: str
    category: str
    name: str
    description: str
    pricing_model: str
    price_from_rub: Decimal
    price_unit: str
    compliance_tags: list[str] = Field(default_factory=list)
    tech_tags: list[str] = Field(default_factory=list)


class ServicePackage(BaseModel):
    provider: Provider
    services: list[Service]
