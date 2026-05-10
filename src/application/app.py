from __future__ import annotations

import asyncio
import logging

import uvicorn
from fastapi import FastAPI
from openai import AsyncOpenAI
from yoyo import get_backend, read_migrations

from src.adapters.embedding import SentenceTransformerEmbedder
from src.adapters.geocoding import NominatimGeocoder
from src.adapters.llm import YandexGPTAdapter
from src.adapters.providers.cloudru_web import CloudRuWebProvider
from src.adapters.providers.selectel_web import SelectelWebProvider
from src.adapters.providers.t1_local import T1LocalCloudProvider
from src.adapters.providers.t1_web import T1WebCloudProvider
from src.adapters.providers.yandex_cloud_web import YandexCloudWebProvider
from src.adapters.repository import PostgresChatRepository, PostgresServiceRepository
from src.controller.restapi.v1.ranking.router import get_html_router, get_router
from src.models import Provider
from src.service import ChatService, ProviderSyncWorker
from src.service.agent import RankingAgent
from src.service.mock_score import MockRelevanceScorer

import psycopg_pool

from src.config.config import Settings


logger = logging.getLogger(__name__)


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
            providers.append(
                T1LocalCloudProvider(
                    data_dir=settings.data_dir,
                    client=self.openai_client,
                    model=settings.yandex_model,
                    provider_defaults=_t1_provider_defaults,
                )
            )

        if settings.use_t1_web_provider:
            providers.append(
                T1WebCloudProvider(
                    client=self.openai_client,
                    model=settings.yandex_model,
                    provider_defaults=_t1_provider_defaults,
                )
            )

        if settings.use_cloudru_provider:
            providers.append(
                CloudRuWebProvider(
                    client=self.openai_client,
                    model=settings.yandex_model,
                    provider_defaults=Provider(
                        provider_id="cloud-ru",
                        name="Cloud.ru",
                        base_platform="Evolution",
                        regions=["Москва"],
                    ),
                )
            )

        if settings.use_yandex_cloud_provider:
            providers.append(
                YandexCloudWebProvider(
                    provider_defaults=Provider(
                        provider_id="yandex-cloud",
                        name="Yandex Cloud",
                        base_platform="Yandex Cloud",
                        regions=["Москва"],
                    ),
                )
            )

        if settings.use_selectel_provider:
            providers.append(
                SelectelWebProvider(
                    provider_defaults=Provider(
                        provider_id="selectel",
                        name="Selectel",
                        base_platform="Selectel",
                        regions=["Москва", "Санкт-Петербург"],
                    ),
                )
            )

        pool = psycopg_pool.AsyncConnectionPool(
            settings.postgres_dsn,
            min_size=1,
            max_size=10,
            open=False,
        )
        self.pool = pool

        self.embedder = SentenceTransformerEmbedder(model_name=settings.embedding_model)
        self.repository = PostgresServiceRepository(dsn=settings.postgres_dsn)
        self.chat_repo = PostgresChatRepository(pool=pool)  # type: ignore
        self.geocoder = NominatimGeocoder()

        self.llm = YandexGPTAdapter(
            client=self.openai_client,
            model=settings.yandex_model,
        )

        self.scorer = MockRelevanceScorer()

        self.agent = RankingAgent(
            llm=self.llm,
            geocoder=self.geocoder,
            scorer=self.scorer,
            chat_repo=self.chat_repo,
        )

        self.chat_service = ChatService(
            chat_repo=self.chat_repo,
            agent=self.agent,
        )

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

    def create_fastapi_app(self) -> FastAPI:
        app = FastAPI(title="Provider Ranking Agent")
        app.include_router(get_router(self.chat_service, self.agent), prefix="/api/v1")
        app.include_router(get_html_router())
        return app

    async def run_once(self) -> None:
        await self.pool.open()
        try:
            await self.worker.run_once()
        finally:
            await self.pool.close()

    async def run_forever(self) -> None:
        await self.pool.open()
        fastapi_app = self.create_fastapi_app()
        config = uvicorn.Config(
            app=fastapi_app,
            host=self.settings.host,
            port=self.settings.port,
            log_level="info",
        )
        server = uvicorn.Server(config)

        try:
            await asyncio.gather(
                server.serve(),
                self.worker.run_forever(),
            )
        finally:
            await self.pool.close()


def build_application(settings: Settings | None = None) -> Application:
    return Application(settings or Settings.from_env())
