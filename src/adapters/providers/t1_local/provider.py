from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from openai import AsyncOpenAI
from pypdf import PdfReader

from src.models import Provider, Service, ServicePackage

logger = logging.getLogger(__name__)

_MAX_CHARS = 14_000
_MAX_CHARS_TARIFF = 56_000

# Matches top-level group headings in the tariff document.
# Examples: "1. Группа услуг Compute", "9. Программные услуги ...", "13. Услуга X", "8. Сетевые услуги"
_GROUP_RE = re.compile(
    r'(?=\n\s*\d{1,2}\.\s+(?:Группа услуг|Программные услуги|Услуга |Сетевые услуги))',
    re.MULTILINE,
)


def _split_tariff_groups(text: str) -> list[str]:
    """Split full tariff text into per-group chunks for separate LLM calls."""
    chunks = _GROUP_RE.split(text)
    result = [c.strip() for c in chunks if c.strip() and re.match(r'\d{1,2}\.', c.strip())]
    return result if result else [text]


def _parse_llm_json(content: str, source: str) -> list[dict]:
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```", 2)[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        logger.error("Invalid JSON from LLM for %s: %s", source, content[:200])
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and "service_id" in payload:
        return [payload]
    logger.warning("Unexpected LLM response shape for %s", source)
    return []

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
  "tech_tags": ["тег1", "тег2"],
  "regions": ["<город/регион, где доступна услуга>"]
}
Или JSON-массив таких объектов если услуг несколько.
regions: список городов/регионов России на русском языке (например ["Москва", "Санкт-Петербург"]).
Если регион не упомянут явно — укажи ["Москва"] как дефолтный для T1 Cloud."""

# Prompt for the tariff document (Prilozhenie_1)
TARIFF_PROMPT = """Ты извлекаешь КАЖДУЮ отдельную строку из тарифных таблиц документа как самостоятельную услугу.

СТРУКТУРА ДОКУМЕНТА:
Документ состоит из пронумерованных групп («N. Группа услуг X»). В каждой группе — таблица со столбцами:
  № | Наименование услуг | Единица измерения | Стоимость за единицу в минуту, руб. БЕЗ НДС | Стоимость за единицу в месяц, руб. БЕЗ НДС

ПРАВИЛА ИЗВЛЕЧЕНИЯ:
1. Каждая строка таблицы → отдельный объект в результирующем массиве.
2. name: точно как в колонке «Наименование услуг» (можно сократить до 120 символов).
3. service_id: CamelCase slug латиницей. Строй как <ПрефиксГруппы>_<ПрефиксСтроки>.
   Префиксы групп:
   - «Compute (Cloud Engine)»            → CloudEngine
   - «GPUaaS (Cloud Engine)»             → CloudEngineGPU
   - «Compute (Cloud Director)»          → CloudDirector
   - «Compute (Cloud Director PAYG)»     → CloudDirectorPAYG
   - «GPUaaS (Cloud Director)»           → CloudDirectorGPU
   - «Хранение и резервное копирование»  → Storage
   - «Выделенный сервер» / HaaS          → HaaS
   - «Сетевые услуги»                    → Network
   - «Microsoft»                         → MsLicense
   - «Astra Linux»                       → AstraLinux
   - «Альт Сервер» / «Альт»             → AltServer
   - «РЕД ОС»                            → RedOs
   - любая другая группа                 → <транслит первых слов>
   Пример: группа «Compute (Cloud Engine)», строка «vCPU a1» → service_id: «CloudEngine_vCpuA1»
4. category:
   - Compute/GPUaaS группы (Cloud Engine, Cloud Director) → «Compute» или «GPUaaS»
   - Хранение / резервное копирование → «Storage» или «Backup» (по смыслу строки)
   - HaaS → «Compute»
   - Сетевые / CDN → «Networking» или «CDN»
   - Программные лицензии (Microsoft, Astra, Альт, РЕД ОС) → «Software»
5. price_from_rub: значение из «Стоимость за единицу в минуту» если есть (не «-»), иначе из «Стоимость за единицу в месяц». Число, не строка. Запятую заменяй на точку.
6. pricing_model: «per-minute» если использована поминутная цена, «per-month» если только месячная.
7. price_unit: значение из колонки «Единица измерения» (шт, ГБ, пользователь, вирт. машина и т.д.).
8. description: 1 предложение — укажи группу и суть ресурса.
9. tech_tags: серия процессора / GPU / ОС / тип диска из названия строки, если есть.
10. compliance_tags: [].

11. regions: список городов/регионов России где доступна услуга (на русском). Если не указано явно — ["Москва"].

Верни СТРОГО валидный JSON-массив (без markdown, без пояснений):
[{
  "service_id": "...",
  "category": "...",
  "name": "...",
  "description": "...",
  "pricing_model": "per-minute|per-month",
  "price_from_rub": <число>,
  "price_unit": "...",
  "compliance_tags": [],
  "tech_tags": [],
  "regions": ["Москва"]
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

    async def _llm_chunk(self, text: str, system_prompt: str, label: str) -> list[Service]:
        async with self._semaphore:
            logger.info("Processing chunk [%s]", label)
            response = await self._client.chat.completions.create(
                model=self._model,
                temperature=0.0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
            )
        items = _parse_llm_json(response.choices[0].message.content or "{}", label)
        services = []
        for item in items:
            try:
                services.append(Service.model_validate(item))
            except Exception:
                logger.exception("Failed to parse service from %s: %s", label, item)
        return services

    async def _extract_from_file(self, path: Path) -> list[Service]:
        text = await asyncio.get_running_loop().run_in_executor(None, _read_file, path)
        is_tariff = _is_tariff_file(path)
        text = text[:(_MAX_CHARS_TARIFF if is_tariff else _MAX_CHARS)]

        if not text.strip():
            logger.warning("Empty text in %s, skipping", path.name)
            return []

        if is_tariff:
            groups = _split_tariff_groups(text)
            logger.info("Processing %s [tariff] — %d group(s)", path.name, len(groups))
            results = await asyncio.gather(*[
                self._llm_chunk(chunk, TARIFF_PROMPT, f"{path.name}:group{i+1}")
                for i, chunk in enumerate(groups)
            ])
            return [svc for batch in results for svc in batch]

        return await self._llm_chunk(
            f"FILE: {path.name}\n\n{text}", SERVICE_PROMPT, path.name
        )

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
