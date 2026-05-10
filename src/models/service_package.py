from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class Provider(BaseModel):
    provider_id: str
    name: str
    base_platform: str
    regions: list[str] = Field(default_factory=list)


class RegionCoord(BaseModel):
    region: str
    lat: float
    lon: float


class Service(BaseModel):
    provider_id: str
    service_id: str
    category: str
    name: str
    description: str
    pricing_model: str
    price_from_rub: Decimal
    price_unit: str
    compliance_tags: list[str] = Field(default_factory=list)
    tech_tags: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    region_coords: list[RegionCoord] = Field(default_factory=list)


class ServicePackage(BaseModel):
    provider: Provider
    services: list[Service]


class UserQuery(BaseModel):
    clean_intent: str
    required_tags: list[str] = Field(default_factory=list)
    max_budget: float | None = None
    location_name: str = ""
    target_lat: float = 0.0
    target_lon: float = 0.0
