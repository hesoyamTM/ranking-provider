from __future__ import annotations

from typing import Protocol

from src.models import Provider, Service, ServicePackage
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

    async def search_by_embedding_top_services(
        self,
        query_embedding: list[float],
    ) -> list[tuple[Service, list[float], list[RegionCoord]]]: ...

    async def search_by_embedding_all_providers(
        self,
        query_embedding: list[float],
    ) -> list[tuple[Service, list[float], list[RegionCoord]]]: ...
