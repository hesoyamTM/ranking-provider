from __future__ import annotations

import asyncio
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from src.models import Provider, Service, ServicePackage

logger = logging.getLogger(__name__)

_BASE = "https://api.selectel.ru"

# Flat /prices/* endpoints — items shaped as
# {resource, group, currency, value, unit, threshold}.
_FLAT_PRICE_ENDPOINTS: tuple[tuple[str, str, str], ...] = (
    # (path, default_category, prefix for service_id)
    ("/prices/ones?currency=rub&without_hidden=true", "Software", "Ones"),
    ("/prices/vmware?currency=rub", "Compute", "Vmware"),
    ("/prices/craas", "ContainerRegistry", "Craas"),
    ("/prices/mks?currency=rub", "Kubernetes", "Mks"),
    ("/prices/dbaas?currency=rub", "Database", "Dbaas"),
    ("/prices/storage?currency=rub", "Storage", "Storage"),
    ("/prices/vpc?currency=rub", "Compute", "Vpc"),
    ("/prices/netdisk?currency=rub", "Storage", "Netdisk"),
)

# /servers/v2/pub/service* endpoints — items shaped as
# {uuid, name, model, tariff_line, tag_list, price_collection.<CCY>.{year,month,day,hour}, ...}.
_SERVICE_ENDPOINTS: tuple[tuple[str, str], ...] = (
    (
        "/servers/v2/pub/service"
        "?model=equipment&model=network&full_view=false&is_order=true&is_hidden=false",
        "Equipment",
    ),
    (
        "/servers/v2/pub/service"
        "?model=software&full_view=false&is_order=true&is_hidden=false",
        "Software",
    ),
    (
        "/servers/v2/pub/service/network?full_view=false&is_order=true&is_hidden=false",
        "Networking",
    ),
    # /colocation is handled separately so we can also fetch per-uuid detail
    # to harvest the addition[] array (only present in the detail view).
    ("/servers/v2/pub/service/lite/server?is_hidden=false", "Compute"),
    ("/servers/v2/pub/service/lite/serverchip?is_hidden=false", "Compute"),
)

# Generic billing prices catalogue.
# Items: {id, name, name_en, prices: {rub, eur, ...}, repeat, subtype, status_id, ...}.
_BILLING_PRICES_PATH = "/v2/billing/prices"

# Detail endpoint per colocation item — only this view exposes addition[].
_COLOCATION_DETAIL_PATH = "/servers/v2/pub/service/colocation/{uuid}"

# status_id values that mean the tariff is orderable / active.
_BILLING_ACTIVE_STATUS_IDS: frozenset[int] = frozenset({2, 3, 4})

# Max concurrent detail fetches for colocation/{uuid}.
_DETAIL_CONCURRENCY = 8

_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")

# How to translate a flat-price unit into our Service.pricing_model value.
_UNIT_PRICING_MODEL: dict[str, str] = {
    "MB": "per-mb",
    "GB": "per-gb",
    "MB*H": "per-mb-hour",
    "GB*H": "per-gb-hour",
    "item": "per-item",
}

# Category overrides for /prices/vpc — its resources span compute, networking,
# storage, etc. The default is "Compute"; refine when prefix matches.
_VPC_CATEGORY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("volume_", "Storage"),
    ("snapshot_", "Storage"),
    ("share_", "Storage"),
    ("image_", "Storage"),
    ("network_", "Networking"),
    ("private_dns", "Networking"),
    ("private_zone", "Networking"),
    ("exttraffic", "Networking"),
    ("load_balancers", "Networking"),
    ("license_", "Software"),
)

# Category overrides for /prices/storage by resource keywords.
_STORAGE_CATEGORY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("traffic", "Networking"),
    ("legacy-traffic", "Networking"),
    ("storage-traffic", "Networking"),
    ("storage-akamai", "CDN"),
)


def _to_camel(value: str) -> str:
    parts = _NON_ALNUM_RE.split(value or "")
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _vpc_category(resource: str) -> str:
    for prefix, cat in _VPC_CATEGORY_PREFIXES:
        if resource.startswith(prefix):
            return cat
    return "Compute"


def _storage_category(resource: str) -> str:
    for prefix, cat in _STORAGE_CATEGORY_PREFIXES:
        if resource.startswith(prefix):
            return cat
    return "Storage"


def _flat_category(default: str, resource: str) -> str:
    if not resource:
        return default
    # /prices/vpc and /prices/vmware default to Compute but span many domains.
    if default == "Compute":
        return _vpc_category(resource)
    if default == "Storage":
        return _storage_category(resource)
    return default


class SelectelWebProvider:
    """Pulls public Selectel price catalogues and maps each tariff row to a
    Service. No LLM — every endpoint returns structured JSON."""

    def __init__(
        self,
        provider_defaults: Provider,
        http_timeout: float = 60.0,
    ) -> None:
        self._defaults = provider_defaults
        self._http_timeout = http_timeout

    # ---------- HTTP ----------

    async def _get_json(self, http: httpx.AsyncClient, path: str) -> Any:
        url = f"{_BASE}{path}" if path.startswith("/") else path
        try:
            resp = await http.get(url)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            logger.exception("Selectel: failed to fetch %s", url)
            return None

    # ---------- Flat /prices/* endpoints ----------

    def _flat_to_service(
        self, item: dict[str, Any], default_category: str, id_prefix: str
    ) -> Service | None:
        resource = (item.get("resource") or "").strip()
        if not resource:
            return None
        group = (item.get("group") or "").strip()
        unit = item.get("unit") or ""
        value = _decimal(item.get("value")) or Decimal("0")
        category = _flat_category(default_category, resource)
        pricing_model = _UNIT_PRICING_MODEL.get(unit, "other")

        sid_parts = [id_prefix, _to_camel(resource)]
        if group:
            sid_parts.append(_to_camel(group))
        service_id = f"Selectel_{'_'.join(p for p in sid_parts if p)}"

        tech_tags = [t for t in (resource, group) if t]

        return Service(
            service_id=service_id,
            category=category,
            name=f"Selectel {id_prefix} {resource}".strip(),
            description=f"Selectel {id_prefix} resource '{resource}'"
            + (f" (group {group})" if group else ""),
            pricing_model=pricing_model,
            price_from_rub=value,
            price_unit=unit or "item",
            compliance_tags=[],
            tech_tags=tech_tags,
            regions=list(self._defaults.regions) or ["Москва"],
        )

    async def _fetch_flat(
        self,
        http: httpx.AsyncClient,
        path: str,
        default_category: str,
        id_prefix: str,
    ) -> list[Service]:
        data = await self._get_json(http, path)
        if not isinstance(data, list):
            return []
        out: list[Service] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                svc = self._flat_to_service(item, default_category, id_prefix)
            except Exception:
                logger.exception("Selectel: bad flat row from %s: %s", path, item)
                continue
            if svc is not None:
                out.append(svc)
        return out

    # ---------- /v2/billing/prices ----------

    def _billing_to_service(self, item: dict[str, Any]) -> Service | None:
        name = item.get("name_en") or item.get("name") or ""
        if not name:
            return None
        status_id = item.get("status_id")
        if isinstance(status_id, int) and status_id not in _BILLING_ACTIVE_STATUS_IDS:
            return None

        # prices.rub is an integer; the schema notes that the unit (RUB or
        # kopeks) depends on context, so we keep the raw value as Decimal.
        prices = item.get("prices") or {}
        rub = _decimal(prices.get("rub")) or Decimal("0")
        item_id = item.get("id")

        repeat = item.get("repeat")
        subtype = item.get("subtype")
        pricing_model = "per-month" if repeat == 1 else "per-item"

        tech_tags: list[str] = []
        for key in ("cpu", "hdd", "memory"):
            val = item.get(key)
            if isinstance(val, str) and val:
                tech_tags.append(val)
        cores = item.get("cores")
        if cores:
            tech_tags.append(f"{cores} cores")
        for tag in item.get("tags") or []:
            if isinstance(tag, dict):
                key = tag.get("key")
                if isinstance(key, str) and key:
                    tech_tags.append(key)
        if subtype == 2:
            tech_tags.append("addition")

        sid_seed = _to_camel(name) or (str(item_id) if item_id is not None else "")
        if not sid_seed:
            return None

        return Service(
            service_id=f"Selectel_Billing_{sid_seed}",
            category="Other",
            name=name,
            description=item.get("name") or name,
            pricing_model=pricing_model,
            price_from_rub=rub,
            price_unit="item",
            compliance_tags=[],
            tech_tags=tech_tags,
            regions=list(self._defaults.regions) or ["Москва"],
        )

    async def _fetch_billing_prices(self, http: httpx.AsyncClient) -> list[Service]:
        data = await self._get_json(http, _BILLING_PRICES_PATH)
        if not isinstance(data, list):
            return []
        out: list[Service] = []
        for it in data:
            if not isinstance(it, dict):
                continue
            try:
                svc = self._billing_to_service(it)
            except Exception:
                logger.exception("Selectel: bad billing row: %s", it)
                continue
            if svc is not None:
                out.append(svc)
        return out

    # ---------- /servers/v2/pub/service* ----------

    @staticmethod
    def _service_price(item: dict[str, Any]) -> tuple[Decimal, str, str]:
        """Pick the smallest-period non-null RUB price; return (price, unit, model)."""
        rub = ((item.get("price_collection") or {}).get("RUB") or {})
        # Order: prefer hour > day > month > year (smallest period first)
        for key, model, unit in (
            ("hour", "per-hour", "hour"),
            ("day", "per-day", "day"),
            ("month", "per-month", "month"),
            ("year", "per-year", "year"),
        ):
            v = _decimal(rub.get(key))
            if v is not None and v > 0:
                return v, unit, model
        return Decimal("0"), "month", "per-month"

    def _service_item_to_service(
        self, item: dict[str, Any], default_category: str
    ) -> Service | None:
        uuid = item.get("uuid")
        name = item.get("name") or ""
        if not name and not uuid:
            return None
        model = (item.get("model") or "").lower()
        tariff_line = item.get("tariff_line") or ""
        config_name = item.get("config_name") or ""

        category = default_category
        if model == "colocation":
            category = "Colocation"
        elif model == "network":
            category = "Networking"
        elif model == "software":
            category = "Software"
        elif model in ("server", "serverchip", "equipment"):
            category = "Compute"

        price_rub, unit, pricing_model = self._service_price(item)

        tech_tags: list[str] = []
        if model:
            tech_tags.append(model)
        if tariff_line:
            tech_tags.append(tariff_line)
        if config_name:
            tech_tags.append(config_name)
        tags = item.get("tags")
        if isinstance(tags, list):
            for t in tags:
                if isinstance(t, str) and t:
                    tech_tags.append(t)
        for tg in item.get("tag_list") or []:
            if isinstance(tg, dict):
                t = tg.get("name") or tg.get("text")
                if isinstance(t, str) and t:
                    tech_tags.append(t)

        sid_seed = uuid or name
        return Service(
            service_id=f"Selectel_Svc_{_to_camel(sid_seed)}",
            category=category,
            name=name or str(uuid),
            description=config_name or name or str(uuid),
            pricing_model=pricing_model,
            price_from_rub=price_rub,
            price_unit=unit,
            compliance_tags=[],
            tech_tags=tech_tags,
            regions=list(self._defaults.regions) or ["Москва"],
        )

    async def _fetch_service_endpoint(
        self, http: httpx.AsyncClient, path: str, default_category: str
    ) -> list[Service]:
        data = await self._get_json(http, path)
        if not isinstance(data, dict):
            return []
        result = data.get("result")
        # /servers/v2/pub/service/colocation/<uuid> returns a single object,
        # while list endpoints return an array.
        if isinstance(result, dict):
            entries: list[dict[str, Any]] = [result]
            for nested_key in ("addition", "primary"):
                nested = result.get(nested_key)
                if isinstance(nested, list):
                    entries.extend(x for x in nested if isinstance(x, dict))
        elif isinstance(result, list):
            entries = [x for x in result if isinstance(x, dict)]
        else:
            return []

        out: list[Service] = []
        for item in entries:
            try:
                svc = self._service_item_to_service(item, default_category)
            except Exception:
                logger.exception(
                    "Selectel: bad service row from %s: %s", path, item.get("uuid")
                )
                continue
            if svc is not None:
                out.append(svc)
        return out

    async def _fetch_colocation_addition(
        self, http: httpx.AsyncClient, uuid: str, sem: asyncio.Semaphore
    ) -> list[Service]:
        async with sem:
            data = await self._get_json(
                http, _COLOCATION_DETAIL_PATH.format(uuid=uuid)
            )
        if not isinstance(data, dict):
            return []
        result = data.get("result")
        if not isinstance(result, dict):
            return []
        additions = result.get("addition") or []
        if not isinstance(additions, list):
            return []
        out: list[Service] = []
        for it in additions:
            if not isinstance(it, dict):
                continue
            try:
                svc = self._service_item_to_service(it, "Colocation")
            except Exception:
                logger.exception(
                    "Selectel: bad colocation addition under %s: %s",
                    uuid, it.get("uuid"),
                )
                continue
            if svc is not None:
                out.append(svc)
        return out

    async def _fetch_colocation(self, http: httpx.AsyncClient) -> list[Service]:
        data = await self._get_json(http, "/servers/v2/pub/service/colocation")
        if not isinstance(data, dict):
            return []
        result = data.get("result")
        if not isinstance(result, list):
            return []

        base_services: list[Service] = []
        uuids: list[str] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            try:
                svc = self._service_item_to_service(item, "Colocation")
            except Exception:
                logger.exception(
                    "Selectel: bad colocation row: %s", item.get("uuid")
                )
                continue
            if svc is not None:
                base_services.append(svc)
            uid = item.get("uuid")
            if isinstance(uid, str) and uid:
                uuids.append(uid)

        if not uuids:
            return base_services

        sem = asyncio.Semaphore(_DETAIL_CONCURRENCY)
        addition_batches = await asyncio.gather(
            *[self._fetch_colocation_addition(http, u, sem) for u in uuids]
        )
        for batch in addition_batches:
            base_services.extend(batch)
        return base_services

    # ---------- entrypoint ----------

    async def fetch(self) -> ServicePackage:
        timeout = httpx.Timeout(self._http_timeout)
        headers = {"User-Agent": "Mozilla/5.0 (provider-ranking-agent/1.0)"}

        async with httpx.AsyncClient(
            timeout=timeout, headers=headers, follow_redirects=True
        ) as http:
            tasks: list[asyncio.Task[list[Service]]] = []
            for path, cat, prefix in _FLAT_PRICE_ENDPOINTS:
                tasks.append(asyncio.create_task(self._fetch_flat(http, path, cat, prefix)))
            for path, cat in _SERVICE_ENDPOINTS:
                tasks.append(asyncio.create_task(self._fetch_service_endpoint(http, path, cat)))
            tasks.append(asyncio.create_task(self._fetch_colocation(http)))
            tasks.append(asyncio.create_task(self._fetch_billing_prices(http)))
            batches = await asyncio.gather(*tasks)

        services: list[Service] = []
        seen: set[str] = set()
        for batch in batches:
            for svc in batch:
                if svc.service_id in seen:
                    continue
                seen.add(svc.service_id)
                services.append(svc)

        logger.info("Selectel: produced %d unique service(s)", len(services))
        return ServicePackage(provider=self._defaults, services=services)
