from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from openai import AsyncOpenAI
from pypdf import PdfReader

from src.models import Provider, Service, ServicePackage

logger = logging.getLogger(__name__)

_MAX_CHARS = 14_000

# Prompt for service description documents (Prilozhenie 6.x)
SERVICE_PROMPT = """Ты извлекаешь структурированные данные об облачных услугах из документа-описания услуги.

ПРАВИЛА:
- Документ описывает конкретную облачную услугу (или несколько вариантов одной услуги)
- Имя файла — подсказка: например "Prilozhenie_6.4._Obyektnoe_khranilische_S3.pdf" → услуга S3-хранилище
- Если в документе явно описаны НЕСКОЛЬКО самостоятельных вариантов услуги с разными характеристиками (например, обычный S3 и мультизональный S3) — верни массив объектов
- НЕ создавай отдельные услуги для тарифных строк, конфигураций CPU/RAM, размеров дисков
- price_from_rub: минимальная цена из тарифной таблицы документа, 0 если не указана
- compliance_tags: только явно упомянутые стандарты (152-ФЗ, PCI-DSS, ISO 27001 и т.д.)

Верни СТРОГО валидный JSON (без markdown, без пояснений):
{
  "service_id": "<CamelCase slug латиницей>",
  "category": "<Networking|Compute|Storage|Database|Security|ML|CDN|Backup|DevOps|Messaging|GPUaaS>",
  "name": "<название услуги>",
  "description": "<1-3 предложения описания>",
  "pricing_model": "<per-month|per-minute|per-gb|per-request|free>",
  "price_from_rub": <число>,
  "price_unit": "<единица тарификации>",
  "compliance_tags": [],
  "tech_tags": ["тег1", "тег2"]
}
Или JSON-массив таких объектов если услуг несколько."""

# Prompt for the tariff document (Prilozhenie_1)
TARIFF_PROMPT = """Ты извлекаешь услуги из тарифного приложения облачного провайдера.

ПРАВИЛА:
- Извлекай только ГРУППЫ УСЛУГ верхнего уровня как отдельные услуги
- НЕ создавай отдельные записи для каждой строки тарифа (CPU, RAM, диски — это не услуги)
- Для каждой группы услуг верни ОДНУ запись с минимальной ценой из группы
- Пропускай группы Compute и GPUaaS — у них есть отдельные файлы описания
- Включай только группы без отдельных файлов описания: HaaS (Выделенный сервер), сетевые услуги если есть

Верни СТРОГО валидный JSON-массив (без markdown, без пояснений):
[{
  "service_id": "<CamelCase slug латиницей>",
  "category": "<Compute|Networking|Storage|Backup|CDN>",
  "name": "<название группы услуг>",
  "description": "<краткое описание>",
  "pricing_model": "<per-month|per-minute>",
  "price_from_rub": <минимальная цена из группы, число>,
  "price_unit": "<единица>",
  "compliance_tags": [],
  "tech_tags": []
}]"""


def _is_tariff_file(path: Path) -> bool:
    name = path.stem.lower()
    return "tarifn" in name or name.startswith("prilozhenie_1_")


def _read_file(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages)
    return path.read_text(encoding="utf-8")


class T1LocalCloudProvider:
    """Читает файлы из data/t1 и извлекает услуги через YandexGPT (OpenAI-совместимый API)."""

    _SUPPORTED = {".md", ".txt", ".json", ".html", ".pdf"}

    def __init__(
        self,
        data_dir: Path,
        client: AsyncOpenAI,
        model: str,
        provider_defaults: Provider,
        max_concurrent: int = 4,
    ) -> None:
        self._data_dir = data_dir
        self._client = client
        self._model = model
        self._defaults = provider_defaults
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def _source_files(self) -> list[Path]:
        if not self._data_dir.exists():
            raise FileNotFoundError(f"Data dir not found: {self._data_dir}")
        return sorted(
            p for p in self._data_dir.iterdir()
            if p.is_file() and p.suffix.lower() in self._SUPPORTED
        )

    async def _extract_from_file(self, path: Path) -> list[Service]:
        text = await asyncio.get_running_loop().run_in_executor(None, _read_file, path)
        text = text[:_MAX_CHARS]
        if not text.strip():
            logger.warning("Empty text in %s, skipping", path.name)
            return []

        is_tariff = _is_tariff_file(path)
        system_prompt = TARIFF_PROMPT if is_tariff else SERVICE_PROMPT

        async with self._semaphore:
            logger.info("Processing %s [%s]", path.name, "tariff" if is_tariff else "service")
            response = await self._client.chat.completions.create(
                model=self._model,
                temperature=0.0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"FILE: {path.name}\n\n{text}"},
                ],
            )

        content = response.choices[0].message.content or "{}"
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```", 2)[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.rsplit("```", 1)[0].strip()

        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            logger.error("Invalid JSON from LLM for %s: %s", path.name, content[:200])
            return []

        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict) and "service_id" in payload:
            items = [payload]
        else:
            logger.warning("Unexpected LLM response shape for %s", path.name)
            return []

        services = []
        for item in items:
            try:
                services.append(Service.model_validate(item))
            except Exception:
                logger.exception("Failed to parse service from %s: %s", path.name, item)
        return services

    async def fetch(self) -> ServicePackage:
        files = self._source_files()
        if not files:
            logger.warning("No source files in %s, returning empty package", self._data_dir)
            return ServicePackage(provider=self._defaults, services=[])

        logger.info("Found %d file(s) to process", len(files))
        results = await asyncio.gather(*[self._extract_from_file(f) for f in files])

        services: list[Service] = []
        seen: set[str] = set()
        for batch in results:
            for svc in batch:
                if svc.service_id not in seen:
                    seen.add(svc.service_id)
                    services.append(svc)

        logger.info("Extracted %d unique service(s)", len(services))
        return ServicePackage(provider=self._defaults, services=services)
