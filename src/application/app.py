from __future__ import annotations

import asyncio
import logging

import uvicorn
from fastapi import FastAPI
from openai import AsyncOpenAI
from yoyo import get_backend, read_migrations

from src.adapters.geocoding import NominatimGeocoder
from src.adapters.llm import YandexGPTAdapter
from src.adapters.providers.cloudru_web import CloudRuWebProvider
from src.adapters.providers.selectel_web import SelectelWebProvider
from src.adapters.providers.t1_local import T1LocalCloudProvider
from src.adapters.providers.t1_web import T1WebCloudProvider
from src.adapters.providers.vkcloud_web.provider import VkCloudWebProvider
from src.adapters.providers.yandex_cloud_web import YandexCloudWebProvider
from src.adapters.repository import (
    PostgresArtifactRepository,
    PostgresChatRepository,
    PostgresServiceRepository,
)
from src.controller.restapi.v1.ranking.router import get_html_router, get_router
from src.models import Provider
from src.service import ChatService, ProviderSyncWorker
from src.service.agent import (
    AgentOrchestrator,
    AgentToolExecutor,
    ArtifactExtractor,
    ClarificationAgent,
    ConfirmationAgent,
    ExtractionAgent,
    IntentClassifier,
    SynthesisAgent,
)
from src.service.scorer import ScoringService

import psycopg_pool

from src.config.config import Settings
from src.adapters.rag import YandexRAGAdapter


logger = logging.getLogger(__name__)


class Application:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        self.openai_client = AsyncOpenAI(
            api_key=settings.yandex_api_key,
            base_url=settings.yandex_base_url,
            project=settings.yandex_folder_id,
        )

        self.ai_studio_client = AsyncOpenAI(
            api_key=settings.yandex_api_key,
            base_url=settings.yandex_ai_studio_base_url,
            project=settings.yandex_folder_id,
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

        if (
            settings.use_vk_cloud_provider
        ):  # Проверь, как называется этот флаг в твоем Settings
            providers.append(
                VkCloudWebProvider(
                    provider_defaults=Provider(
                        provider_id="vk-cloud",
                        name="VK Cloud",
                        base_platform="VK Cloud",
                        regions=["Москва"],
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

        self.repository = PostgresServiceRepository(dsn=settings.postgres_dsn)
        self.chat_repo = PostgresChatRepository(pool=pool)  # type: ignore
        self.artifact_repo = PostgresArtifactRepository(pool=pool)  # type: ignore
        self.geocoder = NominatimGeocoder()

        self.llm = YandexGPTAdapter(
            client=self.openai_client,
            model=settings.yandex_model,
        )

        rag = YandexRAGAdapter(
            client=self.ai_studio_client,
            agent_id=settings.yandex_rag_agent_id,
        )

        self.scorer = ScoringService(
            rag=rag,
        )

        tool_executor = AgentToolExecutor(
            provider_repo=self.repository,
            scorer=self.scorer,
            geocoder=self.geocoder,
        )

        self.agent = AgentOrchestrator(
            chat_repo=self.chat_repo,
            intent=IntentClassifier(llm=self.llm),
            extraction=ExtractionAgent(llm=self.llm),
            clarification=ClarificationAgent(llm=self.llm),
            confirmation=ConfirmationAgent(llm=self.llm),
            synthesis=SynthesisAgent(llm=self.llm, tool_executor=tool_executor),
            artifact_extractor=ArtifactExtractor(llm=self.llm),
            artifact_repo=self.artifact_repo,
        )

        self.chat_service = ChatService(
            chat_repo=self.chat_repo,
            agent=self.agent,
            artifact_repo=self.artifact_repo,
        )

        self.worker = ProviderSyncWorker(
            providers=providers,
            repository=self.repository,
            interval_seconds=settings.sync_interval_seconds,
            geocoder=self.geocoder,
        )

    async def migrate(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._migrate_sync)
        logger.info("Migrations applied")

    def _migrate_sync(self) -> None:
        dsn = self.settings.postgres_dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        backend = get_backend(dsn)
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
