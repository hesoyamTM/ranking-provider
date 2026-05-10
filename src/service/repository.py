from __future__ import annotations

from typing import Protocol

from src.models import Service, ServicePackage
from src.models.service_package import RegionCoord


class ServiceRepository(Protocol):
    async def save_package(
        self,
        package: ServicePackage,
        embeddings: dict[str, list[float]],
    ) -> None: ...

    async def upsert_service(
        self,
        provider_id: str,
        service: Service,
        embedding: list[float],
    ) -> None: ...

    async def search_by_embedding(
        self,
        query_embedding: list[float],
        top_k: int = 50,
    ) -> list[tuple[Service, list[float], list[RegionCoord]]]: ...
