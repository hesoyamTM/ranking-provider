from __future__ import annotations

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from src.models import Provider, Service, ServicePackage

logger = logging.getLogger(__name__)

_API_URL = "https://yandex.cloud/api/priceList/getPriceList"

# Order matters: more specific keywords first.
_PRICING_MODEL_RULES: tuple[tuple[str, str], ...] = (
    ("hour", "per-hour"),
    ("minute", "per-minute"),
    ("month", "per-month"),
    ("request", "per-request"),
    ("gb", "per-gb"),
    ("second", "per-second"),
)

_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")


def _to_camel(value: str) -> str:
    parts = _NON_ALNUM_RE.split(value)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def _pricing_model(pricing_unit: str) -> str:
    lower = (pricing_unit or "").lower()
    for needle, model in _PRICING_MODEL_RULES:
        if needle in lower:
            return model
    return pricing_unit or "other"


def _rates_min_price(rates: list[dict[str, Any]]) -> Decimal:
    """Return the lowest non-zero unit price; if all rates are zero, return 0."""
    prices: list[Decimal] = []
    for rate in rates:
        raw = rate.get("unitPrice")
        if raw is None:
            continue
        try:
            prices.append(Decimal(str(raw)))
        except (InvalidOperation, ValueError):
            continue
    if not prices:
        return Decimal("0")
    positive = [p for p in prices if p > 0]
    return min(positive) if positive else Decimal("0")


class YandexCloudWebProvider:
    """Pulls the public Yandex Cloud price list and maps it to our Service model.

    The endpoint returns one entry per pricing tier ("threshold"), so multiple
    rows can share the same SKU externalId. Entries are grouped by SKU and
    collapsed into a single Service whose price_from_rub is the lowest non-zero
    unit price among that SKU's tiers.
    """

    def __init__(
        self,
        provider_defaults: Provider,
        installation_code: str = "ru",
        currency: str = "RUB",
        lang: str = "ru",
        page_size: int = 1_000_000,
        http_timeout: float = 60.0,
    ) -> None:
        self._defaults = provider_defaults
        self._installation_code = installation_code
        self._currency = currency
        self._lang = lang
        self._page_size = page_size
        self._http_timeout = http_timeout

    async def _fetch_page(
        self, http: httpx.AsyncClient, page_token: str | None
    ) -> dict[str, Any]:
        today = date.today().isoformat()
        params = {
            "installationCode": self._installation_code,
            "from": today,
            "to": today,
            "pageSize": str(self._page_size),
            "currency": self._currency,
            "lang": self._lang,
            "withExpired": "false",
            "withThresholds": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        resp = await http.get(_API_URL, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _fetch_all_skus(self, http: httpx.AsyncClient) -> list[dict[str, Any]]:
        skus: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(50):
            data = await self._fetch_page(http, token)
            batch = data.get("skus") or []
            skus.extend(batch)
            token = data.get("nextPageToken")
            if not token or not batch:
                break
        return skus

    @staticmethod
    def _group_skus(raw: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for entry in raw:
            ext_id = entry.get("externalId")
            if not ext_id:
                continue
            cur = grouped.setdefault(ext_id, {"sku": entry, "rates": []})
            rates = (
                (entry.get("pricingVersion") or {})
                .get("pricingExpression", {})
                .get("rates")
                or []
            )
            cur["rates"].extend(rates)
        return grouped

    def _to_service(
        self, sku: dict[str, Any], rates: list[dict[str, Any]]
    ) -> Service | None:
        sku_name = sku.get("name") or sku.get("externalId") or ""
        if not sku_name:
            return None
        service_info = sku.get("service") or {}
        service_slug = service_info.get("name") or "yandex"
        translated_name = sku.get("translatedName") or sku_name
        description = service_info.get("description") or translated_name
        pricing_unit = sku.get("pricingUnit") or ""
        price_unit = sku.get("translatedPricingUnit") or pricing_unit
        category = service_info.get("group") or "Other"

        tech_tags: list[str] = []
        for tag in (service_slug, sku_name):
            if tag and tag not in tech_tags:
                tech_tags.append(tag)

        return Service(
            service_id=f"YandexCloud_{_to_camel(sku_name)}",
            category=category,
            name=translated_name,
            description=description,
            pricing_model=_pricing_model(pricing_unit),
            price_from_rub=_rates_min_price(rates),
            price_unit=price_unit,
            compliance_tags=[],
            tech_tags=tech_tags,
            regions=list(self._defaults.regions) or ["Москва"],
        )

    async def fetch(self) -> ServicePackage:
        timeout = httpx.Timeout(self._http_timeout)
        headers = {"User-Agent": "Mozilla/5.0 (provider-ranking-agent/1.0)"}

        async with httpx.AsyncClient(
            timeout=timeout, headers=headers, follow_redirects=True
        ) as http:
            raw = await self._fetch_all_skus(http)

        logger.info("Yandex Cloud: fetched %d sku entr(ies)", len(raw))
        grouped = self._group_skus(raw)

        services: list[Service] = []
        seen: set[str] = set()
        for ext_id, payload in grouped.items():
            try:
                svc = self._to_service(payload["sku"], payload["rates"])
            except Exception:
                logger.exception("Failed to map Yandex Cloud sku %s", ext_id)
                continue
            if svc is None or svc.service_id in seen:
                continue
            seen.add(svc.service_id)
            services.append(svc)

        logger.info("Yandex Cloud: produced %d unique service(s)", len(services))
        return ServicePackage(provider=self._defaults, services=services)
