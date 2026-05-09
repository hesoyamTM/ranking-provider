from __future__ import annotations

from typing import Protocol

from src.models import ServicePackage


class CloudProvider(Protocol):
    async def fetch(self) -> ServicePackage: ...
