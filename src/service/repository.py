from __future__ import annotations

from typing import Protocol

from src.models import Service, ServicePackage


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
