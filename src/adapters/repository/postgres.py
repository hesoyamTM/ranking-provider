from __future__ import annotations

import json

import psycopg

from src.models import Provider, Service, ServicePackage


class PostgresServiceRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def _connect(self) -> psycopg.AsyncConnection:
        return await psycopg.AsyncConnection.connect(self._dsn)

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
            (
                provider.provider_id,
                provider.name,
                provider.base_platform,
                provider.regions,
            ),
        )

    @staticmethod
    async def _upsert_service(
        cur: psycopg.AsyncCursor,
        provider_id: str,
        service: Service,
    ) -> None:
        region_coords_json = json.dumps(
            [rc.model_dump() for rc in service.region_coords]
        )
        await cur.execute(
            """
            INSERT INTO services (
                service_id, provider_id, category, name, description,
                pricing_model, price_from_rub, price_unit,
                compliance_tags, tech_tags,
                regions, region_coords
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (provider_id, service_id) DO UPDATE SET
                category = EXCLUDED.category,
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                pricing_model = EXCLUDED.pricing_model,
                price_from_rub = EXCLUDED.price_from_rub,
                price_unit = EXCLUDED.price_unit,
                compliance_tags = EXCLUDED.compliance_tags,
                tech_tags = EXCLUDED.tech_tags,
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
                service.regions,
                region_coords_json,
            ),
        )

    async def save_package(self, package: ServicePackage) -> None:
        async with await self._connect() as conn, conn.cursor() as cur:
            await self._upsert_provider(cur, package.provider)
            for service in package.services:
                await self._upsert_service(
                    cur, package.provider.provider_id, service
                )
            await conn.commit()

    async def list_providers(self) -> list[Provider]:
        async with await self._connect() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                SELECT provider_id, name, base_platform, regions
                FROM providers
                ORDER BY provider_id
                """
            )
            rows = await cur.fetchall()
            return [
                Provider(
                    provider_id=provider_id,
                    name=name,
                    base_platform=base_platform or "",
                    regions=regions or [],
                )
                for (provider_id, name, base_platform, regions) in rows
            ]
