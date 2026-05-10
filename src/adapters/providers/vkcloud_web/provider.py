from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

import httpx
from bs4 import BeautifulSoup

from src.models import Provider, Service, ServicePackage

logger = logging.getLogger(__name__)

_URL = "https://cloud.vk.com/pricelist/"

_CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    ("cloud servers", "Compute"),
    ("виртуальные серверы", "Compute"),
    ("gpu", "GPUaaS"),
    ("object storage", "Storage"),
    ("s3", "Storage"),
    ("kubernetes", "DevOps"),
    ("базы данных", "Database"),
    ("databases", "Database"),
    ("backup", "Backup"),
    ("бэкап", "Backup"),
    ("cdn", "CDN"),
    ("ddos", "Security"),
    ("балансировщик", "Networking"),
)

class VkCloudWebProvider:
    def __init__(self, provider_defaults: Provider) -> None:
        self._defaults = provider_defaults
        self._http_timeout = 30.0

    async def fetch(self) -> ServicePackage:
        headers = {
            "User-Agent": "Mozilla/5.0 (provider-ranking-agent/1.0)",
            "Accept": "text/html,application/xhtml+xml,xml;q=0.9,image/avif,webp,*/*;q=0.8",
        }

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._http_timeout),
            headers=headers,
            follow_redirects=True,
            verify=False
        ) as http:
            response = await http.get(_URL)
            response.raise_for_status()
            html = response.text

        services = self._parse_html_tables(html)

        logger.info("VK Cloud: produced %d unique service(s)", len(services))
        return ServicePackage(provider=self._defaults, services=services)

    def _parse_html_tables(self, html: str) -> list[Service]:
        soup = BeautifulSoup(html, "html.parser")
        services: list[Service] = []
        seen_ids: set[str] = set()

        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            name = cells[0].get_text(strip=True)
            parameter = cells[1].get_text(strip=True)
            price_raw = cells[-1].get_text(strip=True)

            if not any(k in f"{name} {parameter}".lower() for k in ["vcpu", "ram", "гб", "₽"]):
                continue

            price = self._clean_price(price_raw)
            if price == 0: continue

            service_id = f"vk-{re.sub(r'[^a-zA-Z0-9]', '', name).lower()}"
            if service_id in seen_ids: continue

            category = self._guess_category(name)

            services.append(Service(
                service_id=service_id,
                category=category,
                name=f"{name} ({parameter})",
                description=f"Тариф VK Cloud: {name}, ресурс {parameter}",
                pricing_model="per-hour",
                price_from_rub=price,
                price_unit="₽",
                tech_tags=["vk-cloud"],
                regions=list(self._defaults.regions) or ["Москва"],
            ))
            seen_ids.add(service_id)

        return services

    def _guess_category(self, name: str) -> str:
        lower_name = name.lower()
        for needle, category in _CATEGORY_RULES:
            if needle in lower_name:
                return category
        return "Compute"

    def _clean_price(self, price_str: str) -> Decimal:
        cleaned = re.sub(r'[^\d]', '', price_str)
        try:
            return Decimal(cleaned) if cleaned else Decimal("0")
        except InvalidOperation:
            return Decimal("0")