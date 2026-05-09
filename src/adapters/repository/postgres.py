from __future__ import annotations

import psycopg
from pgvector.psycopg import register_vector_async

from src.models import Provider, Service, ServicePackage


class PostgresServiceRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def _connect(self) -> psycopg.AsyncConnection:
        conn = await psycopg.AsyncConnection.connect(self._dsn)
        await register_vector_async(conn)
        return conn

    @staticmethod
    async def _upsert_provider(cur: psycopg.AsyncCursor, provider: Provider) -> None:
        await cur.execute(
            """
            INSERT INTO providers (provider_id, name, base_platform, regions)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (provider_id) DO UPDATE SET
                name = EXCLUDED.name,
                base_platform = EXCLUDED.base_platform,
                regions = EXCLUDED.regions,
                updated_at = NOW()
            """,
            (provider.provider_id, provider.name, provider.base_platform, provider.regions),
        )

    @staticmethod
    async def _upsert_service(
        cur: psycopg.AsyncCursor,
        provider_id: str,
        service: Service,
        embedding: list[float],
    ) -> None:
        await cur.execute(
            """
            INSERT INTO services (
                service_id, provider_id, category, name, description,
                pricing_model, price_from_rub, price_unit,
                compliance_tags, tech_tags, embedding
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (provider_id, service_id) DO UPDATE SET
                category = EXCLUDED.category,
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                pricing_model = EXCLUDED.pricing_model,
                price_from_rub = EXCLUDED.price_from_rub,
                price_unit = EXCLUDED.price_unit,
                compliance_tags = EXCLUDED.compliance_tags,
                tech_tags = EXCLUDED.tech_tags,
                embedding = EXCLUDED.embedding,
                updated_at = NOW()
            """,
            (
                service.service_id,
                provider_id,
                service.category,
                service.name,
                service.description,
                service.pricing_model,
                service.price_from_rub,
                service.price_unit,
                service.compliance_tags,
                service.tech_tags,
                embedding,
            ),
        )

    async def save_package(
        self,
        package: ServicePackage,
        embeddings: dict[str, list[float]],
    ) -> None:
        async with await self._connect() as conn, conn.cursor() as cur:
            await self._upsert_provider(cur, package.provider)
            for service in package.services:
                vec = embeddings.get(service.service_id)
                if vec is None:
                    raise ValueError(f"Missing embedding for service {service.service_id}")
                await self._upsert_service(cur, package.provider.provider_id, service, vec)
            await conn.commit()

    async def upsert_service(
        self,
        provider_id: str,
        service: Service,
        embedding: list[float],
    ) -> None:
        async with await self._connect() as conn, conn.cursor() as cur:
            await self._upsert_service(cur, provider_id, service, embedding)
            await conn.commit()
