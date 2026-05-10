from __future__ import annotations

import json
from decimal import Decimal

import psycopg
from pgvector.psycopg import register_vector_async

from src.models.scoring_package import ServiceDTO
from src.models import Provider, Service, ServicePackage


class PostgresServiceRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def _connect(self) -> psycopg.AsyncConnection:
        conn = await psycopg.AsyncConnection.connect(self._dsn)
        await register_vector_async(conn)
        return conn

    @staticmethod
    def _row_to_service_dto(row) -> ServiceDTO:
        (
            provider_id, service_id, category, name, description,
            pricing_model, price_from_rub, price_unit,
            compliance_tags, tech_tags, score,
        ) = row
        return ServiceDTO(
            provider_id=provider_id,
            service_id=service_id,
            category=category,
            name=name,
            description=description or "",
            pricing_model=pricing_model or "",
            price_from_rub=price_from_rub if price_from_rub is not None else Decimal(0),
            price_unit=price_unit or "",
            compliance_tags=compliance_tags or [],
            tech_tags=tech_tags or [],
            score=float(score),
        )

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
        region_coords_json = json.dumps(
            [rc.model_dump() for rc in service.region_coords]
        )
        await cur.execute(
            """
            INSERT INTO services (
                service_id, provider_id, category, name, description,
                pricing_model, price_from_rub, price_unit,
                compliance_tags, tech_tags, embedding,
                regions, region_coords
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                regions = EXCLUDED.regions,
                region_coords = EXCLUDED.region_coords,
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
                service.regions,
                region_coords_json,
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

    async def search_by_embedding_top_services(
        self,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[ServiceDTO]:
        async with await self._connect() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    s.provider_id,
                    s.service_id,
                    s.category,
                    s.name,
                    s.description,
                    s.pricing_model,
                    s.price_from_rub,
                    s.price_unit,
                    s.compliance_tags,
                    s.tech_tags,
                    1 - (s.embedding <=> %s::vector) AS similarity
                FROM services s
                ORDER BY s.embedding <=> %s::vector ASC
                LIMIT %s
                """,
                (query_embedding, query_embedding, top_k),
            )
            rows = await cur.fetchall()
            return [self._row_to_service_dto(row) for row in rows]

    async def search_by_embedding_all_providers(
        self,
        query_embedding: list[float],
        per_provider: int = 3,
    ) -> list[ServiceDTO]:
        async with await self._connect() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                WITH rankings AS (
                    SELECT
                        s.provider_id,
                        s.service_id,
                        s.category,
                        s.name,
                        s.description,
                        s.pricing_model,
                        s.price_from_rub,
                        s.price_unit,
                        s.compliance_tags,
                        s.tech_tags,
                        1 - (s.embedding <=> %s::vector) AS similarity,
                        ROW_NUMBER() OVER (
                            PARTITION BY s.provider_id
                            ORDER BY s.embedding <=> %s::vector ASC
                        ) AS rn
                    FROM services s
                )
                SELECT
                    provider_id,
                    service_id,
                    category,
                    name,
                    description,
                    pricing_model,
                    price_from_rub,
                    price_unit,
                    compliance_tags,
                    tech_tags,
                    similarity
                FROM rankings
                WHERE rn <= %s
                ORDER BY provider_id, similarity DESC
                """,
                (query_embedding, query_embedding, per_provider),
            )
            rows = await cur.fetchall()
            return [self._row_to_service_dto(row) for row in rows]