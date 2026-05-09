from __future__ import annotations

import asyncio
import logging

import httpx

from src.models import RegionCoord

logger = logging.getLogger(__name__)

# Static coords for cities likely to appear in T1 Cloud documents.
# Avoids unnecessary Nominatim calls for the common case.
_STATIC: dict[str, tuple[float, float]] = {
    "москва": (55.7558, 37.6173),
    "санкт-петербург": (59.9343, 30.3351),
    "спб": (59.9343, 30.3351),
    "новосибирск": (54.9885, 82.9285),
    "екатеринбург": (56.8389, 60.6057),
    "казань": (55.8304, 49.0661),
    "нижний новгород": (56.3269, 44.0059),
    "челябинск": (55.1599, 61.4022),
    "самара": (53.2038, 50.1606),
    "уфа": (54.7388, 55.9721),
    "ростов-на-дону": (47.2357, 39.7015),
    "краснодар": (45.0448, 38.9760),
    "омск": (54.9885, 73.3242),
    "воронеж": (51.6720, 39.1843),
    "пермь": (58.0105, 56.2502),
    "красноярск": (56.0097, 92.7917),
    "владивосток": (43.1056, 131.8735),
    "тюмень": (57.1522, 68.0016),
    "иркутск": (52.2978, 104.2964),
    "хабаровск": (48.4802, 135.0719),
    "волгоград": (48.7080, 44.5133),
    "барнаул": (53.3547, 83.7697),
    "ярославль": (57.6261, 39.8845),
    "владимир": (56.1290, 40.4068),
    "тверь": (56.8587, 35.9176),
    "калуга": (54.5138, 36.2617),
    "тула": (54.1927, 37.6174),
    "рязань": (54.6269, 39.6916),
    "ставрополь": (45.0472, 41.9734),
    "россия": (61.5240, 105.3188),
}

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "provider-ranking-agent/1.0 (contact: ops@t1cloud.ru)"


class NominatimGeocoder:
    """Geocodes region names to (lat, lon).

    Uses a static lookup table for known Russian cities.
    Falls back to Nominatim OSM API for unknown names, with
    a 1 req/sec rate limit and in-memory cache.
    """

    def __init__(self, http_timeout: float = 10.0) -> None:
        self._cache: dict[str, tuple[float, float] | None] = {}
        self._http_timeout = http_timeout
        # Nominatim ToS: max 1 request/second
        self._rate_limit = asyncio.Semaphore(1)

    async def geocode(self, region: str) -> tuple[float, float] | None:
        key = region.strip().lower()
        if key in self._cache:
            return self._cache[key]

        # Static lookup first
        for alias, coords in _STATIC.items():
            if alias in key or key in alias:
                self._cache[key] = coords
                return coords

        # Nominatim fallback
        result = await self._nominatim(region)
        self._cache[key] = result
        return result

    async def _nominatim(self, query: str) -> tuple[float, float] | None:
        async with self._rate_limit:
            try:
                async with httpx.AsyncClient(timeout=self._http_timeout) as http:
                    resp = await http.get(
                        _NOMINATIM_URL,
                        params={"q": query, "format": "json", "limit": 1, "accept-language": "ru"},
                        headers={"User-Agent": _USER_AGENT},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    if data:
                        return float(data[0]["lat"]), float(data[0]["lon"])
            except Exception:
                logger.warning("Nominatim geocoding failed for %r", query)
            # 1 req/sec compliance
            await asyncio.sleep(1.0)
        return None

    async def geocode_batch(self, regions: list[str]) -> dict[str, RegionCoord | None]:
        """Geocode a list of region names; returns {name: RegionCoord | None}."""
        unique = list(dict.fromkeys(regions))  # deduplicate, preserve order
        results: dict[str, RegionCoord | None] = {}
        for region in unique:
            coords = await self.geocode(region)
            results[region] = (
                RegionCoord(region=region, lat=coords[0], lon=coords[1])
                if coords else None
            )
        return results
