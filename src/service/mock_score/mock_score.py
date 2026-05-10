from __future__ import annotations

from decimal import Decimal

from src.models.agent import RankedResource
from src.models.service_package import Service, UserQuery


class MockRelevanceScorer:
    """Моковая реализация RelevanceScorer для тестов и локальной разработки."""

    def __init__(self, services: list[Service] | None = None) -> None:
        self._services = services if services is not None else _default_services()

    def rank_marketplace_resources(
        self,
        query: UserQuery,
    ) -> list[RankedResource]:
        ranked: list[RankedResource] = []
        for index, service in enumerate(self._services):
            score = round(1.0 - index * 0.1, 2)
            ranked.append(
                RankedResource(
                    service=service,
                    score=score,
                    rank=index + 1,
                )
            )
        return ranked


def _default_services() -> list[Service]:
    return [
        Service(
            service_id="mock-vm-1",
            category="compute",
            name="Mock Virtual Machine",
            description="Моковая виртуальная машина для тестов.",
            pricing_model="hourly",
            price_from_rub=Decimal("1.50"),
            price_unit="hour",
            compliance_tags=["152-fz"],
            tech_tags=["linux", "x86_64"],
            regions=["ru-moscow"],
        ),
        Service(
            service_id="mock-storage-1",
            category="storage",
            name="Mock Object Storage",
            description="Моковое объектное хранилище для тестов.",
            pricing_model="monthly",
            price_from_rub=Decimal("0.50"),
            price_unit="gb-month",
            compliance_tags=[],
            tech_tags=["s3"],
            regions=["ru-moscow", "ru-spb"],
        ),
        Service(
            service_id="mock-db-1",
            category="database",
            name="Mock Managed PostgreSQL",
            description="Моковая управляемая PostgreSQL.",
            pricing_model="hourly",
            price_from_rub=Decimal("3.20"),
            price_unit="hour",
            compliance_tags=["152-fz"],
            tech_tags=["postgresql"],
            regions=["ru-moscow"],
        ),
    ]
