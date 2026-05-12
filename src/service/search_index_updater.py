from __future__ import annotations

from typing import Protocol

from src.models import ServicePackage


class SearchIndexUpdater(Protocol):
    """Pushes parsed service packages into an external search index."""

    async def update_index(self, packages: list[ServicePackage]) -> None: ...
