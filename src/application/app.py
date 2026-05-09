from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from openai import AsyncOpenAI
from yoyo import get_backend, read_migrations

from src.adapters.embedding import SentenceTransformerEmbedder
from src.adapters.geocoding import NominatimGeocoder
from src.adapters.providers.cloudru_web import CloudRuWebProvider
from src.adapters.providers.t1_local import T1LocalCloudProvider
from src.adapters.providers.t1_web import T1WebCloudProvider
from src.adapters.providers.yandex_cloud_web import YandexCloudWebProvider
from src.adapters.repository import PostgresServiceRepository
from src.models import Provider
from src.service import ProviderSyncWorker

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    postgres_dsn: str
    yandex_api_key: str
    yandex_model: str
    yandex_base_url: str
    data_dir: Path
    migrations_dir: Path
    sync_interval_seconds: float
    embedding_model: str
    use_t1_local_provider: bool
    use_t1_web_provider: bool
    use_cloudru_provider: bool
    use_yandex_cloud_provider: bool

    @classmethod
    def from_env(cls) -> "Settings":
        folder_id = os.environ.get("YANDEX_FOLDER_ID", "")
        model_name = os.environ.get("YANDEX_MODEL", "yandexgpt-5/latest")
        model = f"gpt://{folder_id}/{model_name}" if folder_id else model_name
        return cls(
            postgres_dsn=os.environ.get(
                "POSTGRES_DSN",
                "postgresql://postgres:postgres@localhost:5432/provider_ranking",
            ),
            yandex_api_key=os.environ.get("YANDEX_API_KEY", ""),
            yandex_model=model,
            yandex_base_url=os.environ.get(
                "YANDEX_BASE_URL", "https://llm.api.cloud.yandex.net/v1"
            ),
            data_dir=Path(os.environ.get("T1_DATA_DIR", "data/t1")).resolve(),
            migrations_dir=Path(os.environ.get("MIGRATIONS_DIR", "migration")).resolve(),
            sync_interval_seconds=float(os.environ.get("SYNC_INTERVAL_SECONDS", "3600")),
            embedding_model=os.environ.get(
                "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
            ),
            use_t1_local_provider=os.environ.get("T1_LOCAL_PROVIDER", "false").lower() == "true",
            use_t1_web_provider=os.environ.get("T1_WEB_PROVIDER", "false").lower() == "true",
            use_cloudru_provider=os.environ.get("CLOUDRU_WEB_PROVIDER", "false").lower() == "true",
            use_yandex_cloud_provider=os.environ.get("YANDEX_CLOUD_WEB_PROVIDER", "false").lower() == "true",
        )


class Application:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        self.openai_client = AsyncOpenAI(
            api_key=settings.yandex_api_key,
            base_url=settings.yandex_base_url,
        )

        _t1_provider_defaults = Provider(
            provider_id="t1-cloud",
            name="Т1 Облако",
            base_platform="OpenStack",
            regions=["Москва"],
        )

        providers = []

        if settings.use_t1_local_provider:
            providers.append(T1LocalCloudProvider(
                data_dir=settings.data_dir,
                client=self.openai_client,
                model=settings.yandex_model,
                provider_defaults=_t1_provider_defaults,
            ))

        if settings.use_t1_web_provider:
            providers.append(T1WebCloudProvider(
                client=self.openai_client,
                model=settings.yandex_model,
                provider_defaults=_t1_provider_defaults,
            ))

        if settings.use_cloudru_provider:
            providers.append(CloudRuWebProvider(
                client=self.openai_client,
                model=settings.yandex_model,
                provider_defaults=Provider(
                    provider_id="cloud-ru",
                    name="Cloud.ru",
                    base_platform="Evolution",
                    regions=["Москва"],
                ),
            ))

        if settings.use_yandex_cloud_provider:
            providers.append(YandexCloudWebProvider(
                provider_defaults=Provider(
                    provider_id="yandex-cloud",
                    name="Yandex Cloud",
                    base_platform="Yandex Cloud",
                    regions=["Москва"],
                ),
            ))

        self.embedder = SentenceTransformerEmbedder(model_name=settings.embedding_model)
        self.repository = PostgresServiceRepository(dsn=settings.postgres_dsn)
        self.geocoder = NominatimGeocoder()

        self.worker = ProviderSyncWorker(
            providers=providers,
            repository=self.repository,
            embedder=self.embedder,
            interval_seconds=settings.sync_interval_seconds,
            geocoder=self.geocoder,
        )

    async def migrate(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._migrate_sync)
        logger.info("Migrations applied")

    def _migrate_sync(self) -> None:
        backend = get_backend(self.settings.postgres_dsn)
        migrations = read_migrations(str(self.settings.migrations_dir))
        with backend.lock():
            backend.apply_migrations(backend.to_apply(migrations))

    async def run_once(self) -> None:
        await self.worker.run_once()

    async def run_forever(self) -> None:
        await self.worker.run_forever()


def build_application(settings: Settings | None = None) -> Application:
    return Application(settings or Settings.from_env())
