from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from src.service.cloud_provider import CloudProvider
from src.service.repository import ServiceRepository

logger = logging.getLogger(__name__)


class Geocoder(Protocol):
    async def geocode_batch(self, regions: list[str]) -> dict: ...


class ProviderSyncWorker:
    def __init__(
        self,
        providers: list[CloudProvider],
        repository: ServiceRepository,
        interval_seconds: float = 3600.0,
        geocoder: Geocoder | None = None,
    ) -> None:
        self._providers = providers
        self._repository = repository
        self._interval = interval_seconds
        self._geocoder = geocoder

    async def _geocode_package(self, package) -> None:
        """Populate region_coords on each service in-place."""
        if not self._geocoder:
            return
        all_regions = list({r for svc in package.services for r in svc.regions})
        if not all_regions:
            return
        coords_map = await self._geocoder.geocode_batch(all_regions)
        for svc in package.services:
            svc.region_coords = [
                coords_map[r] for r in svc.regions if coords_map.get(r) is not None
            ]

    async def _sync_provider(self, provider: CloudProvider) -> None:
        package = await provider.fetch()
        logger.info("Got %d services from %s", len(package.services), package.provider.provider_id)

        await self._geocode_package(package)

        await self._repository.save_package(package)
        logger.info("Saved package for provider %s", package.provider.provider_id)

    async def run_once(self) -> None:
        logger.info("Syncing %d provider(s)", len(self._providers))
        await asyncio.gather(*[self._sync_provider(p) for p in self._providers])

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                logger.info("Worker cancelled, stopping")
                raise
            except Exception:
                logger.exception("Sync iteration failed")
            await asyncio.sleep(self._interval)
