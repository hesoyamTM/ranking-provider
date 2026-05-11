from __future__ import annotations

from typing import Protocol

from src.models import Provider, ServicePackage


class ServiceRepository(Protocol):
    async def save_package(self, package: ServicePackage) -> None: ...

    async def list_providers(self) -> list[Provider]: ...
