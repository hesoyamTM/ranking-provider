from __future__ import annotations
import logging
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import httpx
from bs4 import BeautifulSoup
from src.models import Provider, Service, ServicePackage

logger = logging.getLogger(__name__)

_URL = "https://cloud.vk.com/pricelist/"

_CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    ("cloud servers", "Compute"), ("виртуальные серверы", "Compute"),
    ("gpu", "GPUaaS"), ("object storage", "Storage"), ("s3", "Storage"),
    ("kubernetes", "DevOps"), ("базы данных", "Database"), ("databases", "Database"),
    ("backup", "Backup"), ("бэкап", "Backup"), ("cdn", "CDN"),
    ("ddos", "Security"), ("балансировщик", "Networking"),
)

class VkCloudWebProvider:
    def __init__(self, provider_defaults: Provider) -> None:
        self._defaults = provider_defaults
        self._http_timeout = 30.0

    async def fetch(self) -> ServicePackage:
        headers = {"User-Agent": "Mozilla/5.0 (provider-ranking-agent/1.0)"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(self._http_timeout), headers=headers, follow_redirects=True, verify=False) as http:
            response = await http.get(_URL)
            response.raise_for_status()
            services = self._parse_html_tables(response.text)
        return ServicePackage(provider=self._defaults, services=services)

    def _parse_html_tables(self, html: str) -> list[Service]:
        soup = BeautifulSoup(html, "html.parser")
        services: list[Service] = []
        seen_ids: set[str] = set()

        for table in soup.find_all("table"):
            section_header = table.find_previous(["h2", "h3", "h1"])
            section_name = section_header.get_text(strip=True) if section_header else "Облачные услуги"
            section_name = section_name.replace("Цены на ", "").strip()

            for row in table.find_all("tr"):
                cells = row.find_all("td")

                if len(cells) < 2:
                    continue

                name = cells[0].get_text(strip=True)
                parameter = cells[1].get_text(strip=True)

                price_raw = cells[-1].get_text(strip=True)

                if not any(k in f"{name} {parameter} {price_raw}".lower() for k in ["vcpu", "ram", "гб", "₽", "шт"]):
                    continue

                price = self._clean_price(price_raw)
                if price <= 0:
                    continue

                slug = re.sub(r'[^a-z0-9]', '', (name + parameter).lower())
                service_id = f"vk-{slug[:30]}"

                if service_id in seen_ids:
                    continue

                p_model = "per-month" if "30 дн" in table.get_text().lower() else "per-unit"

                services.append(Service(
                    service_id=service_id,
                    category=self._guess_category(name),
                    name=name,
                    description=f"{section_name}: {name}, ресурс {parameter}",
                    pricing_model=p_model,
                    price_from_rub=price,
                    price_unit="₽",
                    tech_tags=["vk-cloud"],
                    regions=list(self._defaults.regions) or ["Москва"],
                ))
                seen_ids.add(service_id)
        return services

    _MAX_PRICE = Decimal("1e9")

    def _clean_price(self, price_str: str) -> Decimal:
        match = re.search(r'\d[\d\s\u00a0]*(?:[.,]\d+)?', price_str)
        if not match:
            return Decimal("0")
        token = match.group(0).replace('\xa0', '').replace(' ', '').replace(',', '.')
        try:
            value = Decimal(token).quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            return Decimal("0")
        if value >= self._MAX_PRICE:
            logger.warning("vkcloud: price %s exceeds max bound, skipping", value)
            return Decimal("0")
        return value

    def _guess_category(self, name: str) -> str:
        lower_name = name.lower()
        for needle, category in _CATEGORY_RULES:
            if needle in lower_name: return category
        return "Compute"
