from __future__ import annotations

import httpx


class NominatimGeocoder:
    """Адаптер геокодера на базе Nominatim (OpenStreetMap).

    Реализует протокол `Geocoder` из `service.agent.protocols`.
    """

    _URL = "https://nominatim.openstreetmap.org/search"

    def __init__(
        self,
        user_agent: str = "provider-ranking-agent",
        timeout: float = 10.0,
    ) -> None:
        self._user_agent = user_agent
        self._timeout = timeout

    async def get_coordinates(self, location_name: str) -> dict[str, float]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                self._URL,
                params={"q": location_name, "format": "json", "limit": 1},
                headers={"User-Agent": self._user_agent},
            )
            response.raise_for_status()
            data = response.json()

        if not data:
            return {"lat": 0.0, "lon": 0.0}
        return {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"])}
