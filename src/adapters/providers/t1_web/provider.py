from __future__ import annotations

import asyncio
import io
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from openai import AsyncOpenAI
from pypdf import PdfReader

from src.adapters.providers.t1_local.provider import (
    SERVICE_PROMPT,
    TARIFF_PROMPT,
    _MAX_CHARS,
    _MAX_CHARS_TARIFF,
    _parse_llm_json,
    _split_tariff_groups,
)
from src.models import Provider, Service, ServicePackage

logger = logging.getLogger(__name__)
_BASE_URL = "https://t1-cloud.ru"
_SERVICES_PAGE = f"{_BASE_URL}/documents/services"
_RATES_PAGE = f"{_BASE_URL}/documents/rates"

_PDF_HREF_RE = re.compile(r'href=["\']([^"\']*\.pdf[^"\']*)["\']', re.IGNORECASE)

# Filenames that are legal/policy documents, not service descriptions
_SKIP_STEMS = {
    "t1klaud_polzovatelskoe_soglashenie",
    "t1klaud_politika",
}


def _is_service_pdf(url: str) -> bool:
    stem = urlparse(url).path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    return stem not in _SKIP_STEMS


def _is_tariff_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return "tarifn" in stem or stem.startswith("prilozhenie_1_")


def _pdf_text_from_bytes(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _absolute(href: str) -> str:
    if href.startswith("http"):
        return href
    return urljoin(_BASE_URL, href)


class T1WebCloudProvider:
    """Scrapes t1-cloud.ru/documents/services and /rates, downloads PDFs,
    then extracts services via YandexGPT (OpenAI-compatible API)."""

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

    async def _collect_pdf_urls(self, http: httpx.AsyncClient) -> list[str]:
        """Scrape index pages and return deduplicated absolute PDF URLs."""
        seen: set[str] = set()
        urls: list[str] = []

        for page_url in [_SERVICES_PAGE, _RATES_PAGE]:
            resp = await http.get(page_url)
            resp.raise_for_status()
            for href in _PDF_HREF_RE.findall(resp.text):
                abs_url = _absolute(href)
                if abs_url not in seen and _is_service_pdf(abs_url):
                    seen.add(abs_url)
                    urls.append(abs_url)
            logger.info("Found %d PDF link(s) so far after scraping %s", len(urls), page_url)

        return urls

    async def _download(self, http: httpx.AsyncClient, url: str) -> bytes:
        resp = await http.get(url)
        resp.raise_for_status()
        return resp.content

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
                if isinstance(item, dict):
                    item.setdefault("provider_id", self._defaults.provider_id)
                services.append(Service.model_validate(item))
            except Exception:
                logger.exception("Failed to parse service from %s: %s", label, item)
        return services

    async def _extract_from_url(self, http: httpx.AsyncClient, url: str) -> list[Service]:
        filename = url.rsplit("/", 1)[-1]
        try:
            data = await self._download(http, url)
        except Exception:
            logger.exception("Failed to download %s", url)
            return []

        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(None, _pdf_text_from_bytes, data)
        is_tariff = _is_tariff_url(url)
        text = text[:(_MAX_CHARS_TARIFF if is_tariff else _MAX_CHARS)]

        if not text.strip():
            logger.warning("Empty text in %s, skipping", filename)
            return []

        if is_tariff:
            groups = _split_tariff_groups(text)
            logger.info("Processing %s [tariff] — %d group(s)", filename, len(groups))
            results = await asyncio.gather(*[
                self._llm_chunk(chunk, TARIFF_PROMPT, f"{filename}:group{i+1}")
                for i, chunk in enumerate(groups)
            ])
            return [svc for batch in results for svc in batch]

        return await self._llm_chunk(
            f"FILE: {filename}\n\n{text}", SERVICE_PROMPT, filename
        )

    async def fetch(self) -> ServicePackage:
        timeout = httpx.Timeout(self._http_timeout)
        headers = {"User-Agent": "Mozilla/5.0 (provider-ranking-agent/1.0)"}

        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as http:
            urls = await self._collect_pdf_urls(http)
            if not urls:
                logger.warning("No PDF links found on t1-cloud.ru, returning empty package")
                return ServicePackage(provider=self._defaults, services=[])

            logger.info("Downloading and processing %d PDF(s)", len(urls))
            results = await asyncio.gather(*[self._extract_from_url(http, u) for u in urls])

        services: list[Service] = []
        seen: set[str] = set()
        for batch in results:
            for svc in batch:
                if svc.service_id not in seen:
                    seen.add(svc.service_id)
                    services.append(svc)

        logger.info("Extracted %d unique service(s) from web", len(services))
        return ServicePackage(provider=self._defaults, services=services)
