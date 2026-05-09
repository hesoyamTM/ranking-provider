from __future__ import annotations

import asyncio
import logging

from src.service.cloud_provider import CloudProvider
from src.service.embedder import Embedder
from src.service.repository import ServiceRepository

logger = logging.getLogger(__name__)


class ProviderSyncWorker:
    def __init__(
        self,
        providers: list[CloudProvider],
        repository: ServiceRepository,
        embedder: Embedder,
        interval_seconds: float = 3600.0,
    ) -> None:
        self._providers = providers
        self._repository = repository
        self._embedder = embedder
        self._interval = interval_seconds

    async def _sync_provider(self, provider: CloudProvider) -> None:
        package = await provider.fetch()
        logger.info("Got %d services from %s", len(package.services), package.provider.provider_id)

        vectors = await self._embedder.embed_batch(package.services)
        embeddings = {svc.service_id: vec for svc, vec in zip(package.services, vectors)}

        await self._repository.save_package(package, embeddings)
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
