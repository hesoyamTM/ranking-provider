from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from src.models import ServicePackage
from src.service.cloud_provider import CloudProvider
from src.service.repository import ServiceRepository
from src.service.search_index_updater import SearchIndexUpdater

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
        search_index_updater: SearchIndexUpdater | None = None,
    ) -> None:
        self._providers = providers
        self._repository = repository
        self._interval = interval_seconds
        self._geocoder = geocoder
        self._search_index_updater = search_index_updater

    async def _geocode_package(self, package: ServicePackage) -> None:
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

    async def _sync_provider(self, provider: CloudProvider) -> ServicePackage:
        package = await provider.fetch()
        logger.info(
            "Got %d services from %s",
            len(package.services),
            package.provider.provider_id,
        )

        await self._geocode_package(package)

        await self._repository.save_package(package)
        logger.info("Saved package for provider %s", package.provider.provider_id)
        return package

    async def _update_search_index(self, packages: list[ServicePackage]) -> None:
        logger.info("Updating search index")
        if not self._search_index_updater:
            logger.info("Search index updater not configured")
            return
        try:
            await self._search_index_updater.update_index(packages)
            logger.info("Search index updated")
        except Exception:
            logger.exception("Search index update failed")

    async def run_once(self) -> None:
        logger.info("Syncing %d provider(s)", len(self._providers))
        results = await asyncio.gather(
            *[self._sync_provider(p) for p in self._providers],
            return_exceptions=True,
        )
        packages: list[ServicePackage] = []
        for provider, result in zip(self._providers, results):
            if isinstance(result, BaseException):
                logger.error("Provider %r sync failed: %s", provider, result)
                continue
            packages.append(result)

        await self._update_search_index(packages)

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
