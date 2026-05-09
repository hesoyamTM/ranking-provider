from __future__ import annotations

import json
import logging
from pathlib import Path

from openai import AsyncOpenAI

from src.models import Provider, Service, ServicePackage

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """Ты извлекаешь структурированные данные об облачных услугах.
Тебе дают сырой текст про услуги одного провайдера. Верни СТРОГО JSON в формате:
{
  "provider": {
    "provider_id": "<slug>",
    "name": "<название>",
    "base_platform": "<например OpenStack>",
    "regions": ["<регион>", ...]
  },
  "services": [
    {
      "service_id": "<slug>",
      "category": "<Networking|Compute|Storage|Security|...>",
      "name": "<название>",
      "description": "<описание>",
      "pricing_model": "<per-month|per-hour|per-request|...>",
      "price_from_rub": <число>,
      "price_unit": "<единица тарификации>",
      "compliance_tags": ["152-FZ", "PCI-DSS", ...],
      "tech_tags": ["DNS", "VPC", ...]
    }
  ]
}
Никаких пояснений, никакого markdown, только валидный JSON."""


class T1LocalCloudProvider:
    def __init__(
        self,
        data_dir: Path,
        client: AsyncOpenAI,
        model: str,
        provider_defaults: Provider,
    ) -> None:
        self._data_dir = data_dir
        self._client = client
        self._model = model
        self._defaults = provider_defaults

    def _read_sources(self) -> str:
        if not self._data_dir.exists():
            raise FileNotFoundError(f"Data dir not found: {self._data_dir}")
        chunks: list[str] = []
        for path in sorted(self._data_dir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".md", ".txt", ".json", ".html"}:
                continue
            chunks.append(f"### FILE: {path.name}\n{path.read_text(encoding='utf-8')}")
        return "\n\n".join(chunks)

    async def _llm_extract(self, raw: str) -> dict:
        response = await self._client.chat.completions.create(
            model=self._model,
            temperature=0.0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": raw},
            ],
        )
        content = response.choices[0].message.content or "{}"
        # strip possible markdown code fences
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```", 2)[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.rsplit("```", 1)[0].strip()
        return json.loads(content)

    async def fetch(self) -> ServicePackage:
        raw = self._read_sources()
        if not raw.strip():
            logger.warning(
                "No source files in %s, returning empty package", self._data_dir
            )
            return ServicePackage(provider=self._defaults, services=[])

        payload = await self._llm_extract(raw)

        provider_data = {**self._defaults.model_dump(), **payload.get("provider", {})}
        provider = Provider.model_validate(provider_data)
        services = [Service.model_validate(s) for s in payload.get("services", [])]
        return ServicePackage(provider=provider, services=services)
