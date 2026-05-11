from __future__ import annotations

import asyncio
import io
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from openai import AsyncOpenAI
from pypdf import PdfReader

from src.adapters.providers.t1_local.provider import _parse_llm_json
from src.models import Provider, Service, ServicePackage

logger = logging.getLogger(__name__)

_BASE_URL = "https://cloud.ru"
_PRODUCTS_PAGE = f"{_BASE_URL}/products"

# Tariff index has a separate sub-index per platform; each lists per-service
# tariff pages, and each per-service page has a CDN-hosted PDF with the full
# pricing table (the rendered HTML is paginated client-side and only shows
# the first 10 rows, so we always go through the PDF).
_TARIFF_PLATFORMS = ("evolution", "vmware", "advanced", "ml-space", "crs")

_PRODUCT_HREF_RE = re.compile(r'href="(/products/[a-z0-9-]+)"', re.IGNORECASE)
_TARIFF_PAGE_HREF_RE = re.compile(
    r'href="(/documents/tariffs/(?:evolution|vmware|advanced|ml-space|crs)/[a-z0-9-]+)(?:\?[^"]*)?"',
    re.IGNORECASE,
)
_PDF_HREF_RE = re.compile(
    r'href="(https://cdn\.cloud\.ru/docs/[^"]+?\.pdf)"',
    re.IGNORECASE,
)

_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_MAX_CHARS_HTML = 16_000
_MAX_CHARS_TARIFF_PDF = 56_000


def _html_to_text(html: str) -> str:
    html = _SCRIPT_RE.sub(" ", html)
    html = _STYLE_RE.sub(" ", html)
    html = _TAG_RE.sub(" ", html)
    return _WS_RE.sub(" ", html).strip()


def _pdf_text_from_bytes(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _absolute(href: str) -> str:
    if href.startswith("http"):
        return href
    return urljoin(_BASE_URL, href)


SERVICE_PROMPT_HTML = """Ты извлекаешь структурированные данные об облачной услуге Cloud.ru из HTML-текста страницы продукта.

ПРАВИЛА:
- Текст — содержимое страницы /products/<slug> с cloud.ru, описывает один продукт/услугу.
- Игнорируй меню, футер, кнопки «Войти», «Калькулятор» и пр. — бери только основное описание продукта.
- service_id: CamelCase slug латиницей. Можно построить из URL slug (например evolution-compute → EvolutionCompute).
- price_from_rub: 0 если цена не упомянута явно на странице.
- compliance_tags: только явно упомянутые стандарты (152-ФЗ, PCI-DSS, ISO 27001, GDPR и т.п.).
- regions: список упомянутых регионов на русском. Если не указано — ["Москва"].

Верни СТРОГО валидный JSON-объект (без markdown, без пояснений):
{
  "service_id": "<CamelCase slug>",
  "category": "<Networking|Compute|Storage|Database|Security|ML|CDN|Backup|DevOps|Messaging|GPUaaS|Software|Analytics>",
  "name": "<официальное название услуги>",
  "description": "<1-3 предложения, что это за услуга>",
  "pricing_model": "<per-month|per-minute|per-hour|per-gb|per-request|free>",
  "price_from_rub": <число>,
  "price_unit": "<единица тарификации>",
  "compliance_tags": [],
  "tech_tags": ["тег1", "тег2"],
  "regions": ["Москва"]
}"""


CLOUDRU_TARIFF_PROMPT = """Ты извлекаешь КАЖДУЮ строку из тарифной таблицы PDF-документа Cloud.ru как самостоятельную услугу.

СТРУКТУРА ДОКУМЕНТА:
PDF — тарифы одной услуги Cloud.ru (например «Тарифы Evolution Compute»). Таблица содержит колонки:
№ | Наименование работ, услуг | Единица тарификации | Период тарификации | Цена без НДС, руб. | НДС, руб. | Цена с НДС, руб.

ПРАВИЛА ИЗВЛЕЧЕНИЯ:
1. Каждая строка таблицы → отдельный объект массива.
2. service_id: CamelCase slug. Используй имя файла (без расширения) как префикс.
   Пример: файл evolution-compute.pdf, строка «Виртуальная машина 12vCPU/24GB RAM» → "EvolutionCompute_Vm12vCpu24GbRam".
3. name: значение из колонки «Наименование работ, услуг» (можно сократить до 120 символов).
4. price_from_rub: число из колонки «Цена без НДС, руб.». Запятую заменяй на точку. Если только с НДС — бери его.
5. pricing_model: по «Период тарификации»:
   - «час» → "per-hour"
   - «месяц» → "per-month"
   - «минута» → "per-minute"
   - «ГБ» / «GB» → "per-gb"
   - «запрос» / «request» → "per-request"
   - иначе по смыслу.
6. price_unit: значение из «Единица тарификации» (шт, ГБ, IOPS, ядро, vCPU, пользователь и т.п.).
7. category: подбери по смыслу строки и имени файла (Compute, Storage, Networking, Database, ML, GPUaaS, Backup, DevOps, Messaging, Security, CDN, Software, Analytics).
8. description: одно предложение — суть ресурса.
9. tech_tags: серия CPU/GPU, тип диска, ОС из названия, если есть.
10. compliance_tags: [].
11. regions: ["Москва"], если регион не упомянут явно.

Верни СТРОГО валидный JSON-массив (без markdown, без пояснений):
[{
  "service_id": "...",
  "category": "...",
  "name": "...",
  "description": "...",
  "pricing_model": "per-hour|per-month|per-minute|per-gb|per-request|free",
  "price_from_rub": <число>,
  "price_unit": "...",
  "compliance_tags": [],
  "tech_tags": [],
  "regions": ["Москва"]
}]"""


class CloudRuWebProvider:
    """Парсит cloud.ru: страницы /products/<slug> через нейронку как описания услуг
    и тарифные PDF из /documents/tariffs/<platform>/<service> — как прайс-листы."""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        provider_defaults: Provider,
        max_concurrent: int = 4,
        http_timeout: float = 30.0,
    ) -> None:
        self._client = client
        self._model = model
        self._defaults = provider_defaults
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._http_timeout = http_timeout

    async def _collect_product_urls(self, http: httpx.AsyncClient) -> list[str]:
        resp = await http.get(_PRODUCTS_PAGE)
        resp.raise_for_status()
        seen: set[str] = set()
        urls: list[str] = []
        for href in _PRODUCT_HREF_RE.findall(resp.text):
            abs_url = _absolute(href)
            if abs_url not in seen:
                seen.add(abs_url)
                urls.append(abs_url)
        logger.info("Found %d cloud.ru product page(s)", len(urls))
        return urls

    async def _collect_tariff_pdf_urls(self, http: httpx.AsyncClient) -> list[str]:
        sub_pages: set[str] = set()

        for platform in _TARIFF_PLATFORMS:
            index_url = f"{_BASE_URL}/documents/tariffs/{platform}/index"
            try:
                resp = await http.get(index_url)
                resp.raise_for_status()
            except Exception:
                logger.exception("Failed to fetch tariff index %s", index_url)
                continue
            for href in _TARIFF_PAGE_HREF_RE.findall(resp.text):
                if href.endswith("/index"):
                    continue
                sub_pages.add(_absolute(href))

        logger.info("Found %d cloud.ru tariff sub-page(s)", len(sub_pages))

        async def _pdf_from_page(page_url: str) -> list[str]:
            try:
                r = await http.get(page_url)
                r.raise_for_status()
            except Exception:
                logger.exception("Failed to fetch tariff page %s", page_url)
                return []
            return _PDF_HREF_RE.findall(r.text)

        results = await asyncio.gather(*[_pdf_from_page(u) for u in sub_pages])

        seen: set[str] = set()
        pdfs: list[str] = []
        for batch in results:
            for url in batch:
                if url not in seen:
                    seen.add(url)
                    pdfs.append(url)

        logger.info("Found %d cloud.ru tariff PDF(s)", len(pdfs))
        return pdfs

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
        services: list[Service] = []
        for item in items:
            try:
                if isinstance(item, dict):
                    item.setdefault("provider_id", self._defaults.provider_id)
                services.append(Service.model_validate(item))
            except Exception:
                logger.exception("Failed to parse service from %s: %s", label, item)
        return services

    async def _extract_product(self, http: httpx.AsyncClient, url: str) -> list[Service]:
        slug = urlparse(url).path.rsplit("/", 1)[-1]
        try:
            resp = await http.get(url)
            resp.raise_for_status()
        except Exception:
            logger.exception("Failed to fetch product %s", url)
            return []
        text = _html_to_text(resp.text)[:_MAX_CHARS_HTML]
        if not text:
            logger.warning("Empty text on %s", url)
            return []
        return await self._llm_chunk(
            f"URL: {url}\nSLUG: {slug}\n\n{text}", SERVICE_PROMPT_HTML, slug
        )

    async def _extract_tariff_pdf(self, http: httpx.AsyncClient, url: str) -> list[Service]:
        filename = url.rsplit("/", 1)[-1]
        try:
            resp = await http.get(url)
            resp.raise_for_status()
            data = resp.content
        except Exception:
            logger.exception("Failed to download tariff PDF %s", url)
            return []
        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(None, _pdf_text_from_bytes, data)
        text = text[:_MAX_CHARS_TARIFF_PDF]
        if not text.strip():
            logger.warning("Empty text in %s", filename)
            return []
        return await self._llm_chunk(
            f"FILE: {filename}\n\n{text}", CLOUDRU_TARIFF_PROMPT, filename
        )

    async def fetch(self) -> ServicePackage:
        timeout = httpx.Timeout(self._http_timeout)
        headers = {"User-Agent": "Mozilla/5.0 (provider-ranking-agent/1.0)"}

        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as http:
            product_urls, tariff_pdfs = await asyncio.gather(
                self._collect_product_urls(http),
                self._collect_tariff_pdf_urls(http),
            )

            if not product_urls and not tariff_pdfs:
                logger.warning("Nothing found on cloud.ru, returning empty package")
                return ServicePackage(provider=self._defaults, services=[])

            logger.info(
                "Processing %d product page(s) and %d tariff PDF(s) from cloud.ru",
                len(product_urls), len(tariff_pdfs),
            )

            tasks = (
                [self._extract_product(http, u) for u in product_urls]
                + [self._extract_tariff_pdf(http, u) for u in tariff_pdfs]
            )
            results = await asyncio.gather(*tasks)

        services: list[Service] = []
        seen: set[str] = set()
        for batch in results:
            for svc in batch:
                if svc.service_id not in seen:
                    seen.add(svc.service_id)
                    services.append(svc)

        logger.info("Extracted %d unique service(s) from cloud.ru", len(services))
        return ServicePackage(provider=self._defaults, services=services)
