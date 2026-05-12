from __future__ import annotations

import logging
from decimal import Decimal

import httpx
from pydantic import BaseModel, Field

from src.models import Service, ServicePackage


logger = logging.getLogger(__name__)


class YandexFileResponse(BaseModel):
    id: str


class YandexVectorStoreFile(BaseModel):
    id: str


class YandexVectorStoreFileList(BaseModel):
    data: list[YandexVectorStoreFile] = Field(default_factory=list)


class IndexedServiceDocument(BaseModel):
    """Flat representation of a service that is pushed into the vector store."""

    provider_id: str
    provider_name: str
    base_platform: str
    service_id: str
    category: str
    name: str
    description: str
    pricing_model: str
    price_from_rub: Decimal
    price_unit: str
    compliance_tags: list[str] = Field(default_factory=list)
    tech_tags: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)


class IndexDocumentCollection(BaseModel):
    documents: list[IndexedServiceDocument]


class YandexCloudClient:
    """Thin async client for the Yandex Cloud assistants/vector-store API."""

    def __init__(
        self,
        api_key: str,
        folder_id: str,
        base_url: str = "https://ai.api.cloud.yandex.net/v1",
        timeout: float = 60.0,
    ) -> None:
        self._headers = {
            "Authorization": f"Api-Key {api_key}",
            "x-folder-id": folder_id,
            "OpenAI-Project": folder_id,
            "OpenAI-Beta": "assistants=v1",
        }
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def upload_json(self, payload: bytes, filename: str = "services.json") -> str:
        logger.debug("Uploading file '%s' (%d bytes)", filename, len(payload))
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/files",
                headers=self._headers,
                files={"file": (filename, payload, "application/json")},
                data={"purpose": "assistants"},
            )
            resp.raise_for_status()
            file_id = YandexFileResponse.model_validate(resp.json()).id
            logger.debug("Uploaded file '%s' → file_id=%s", filename, file_id)
            return file_id

    async def list_vector_store_files(self, vector_store_id: str) -> list[str]:
        logger.debug("Listing files in vector store %s", vector_store_id)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}/vector_stores/{vector_store_id}/files",
                headers=self._headers,
            )
            resp.raise_for_status()
            parsed = YandexVectorStoreFileList.model_validate(resp.json())
            ids = [f.id for f in parsed.data]
            logger.debug("Vector store %s has %d file(s): %s", vector_store_id, len(ids), ids)
            return ids

    async def detach_and_delete_file(self, vector_store_id: str, file_id: str) -> None:
        logger.debug("Removing old file %s from vector store %s", file_id, vector_store_id)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.delete(
                f"{self._base_url}/vector_stores/{vector_store_id}/files/{file_id}",
                headers=self._headers,
            )
            resp.raise_for_status()
            resp = await client.delete(
                f"{self._base_url}/files/{file_id}",
                headers=self._headers,
            )
            resp.raise_for_status()
        logger.debug("Deleted file %s", file_id)

    async def attach_file(self, vector_store_id: str, file_id: str) -> None:
        logger.debug("Attaching file %s to vector store %s", file_id, vector_store_id)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/vector_stores/{vector_store_id}/files",
                headers=self._headers,
                json={"file_id": file_id},
            )
            resp.raise_for_status()
        logger.debug("Attached file %s to vector store %s", file_id, vector_store_id)


class YandexCloudSearchIndexUpdater:
    """Replaces the contents of a Yandex Cloud vector store with the latest service catalog.

    The data itself is expected to come from upstream callers (e.g. the sync worker)
    via :meth:`update_index` — this class never queries any database directly.
    """

    def __init__(self, client: YandexCloudClient, vector_store_id: str) -> None:
        self._client = client
        self._vector_store_id = vector_store_id

    async def update_index(self, packages: list[ServicePackage]) -> None:
        documents = [
            self._build_document(package, service)
            for package in packages
            for service in package.services
        ]
        if not documents:
            logger.info("No services to index, skipping Yandex Cloud sync")
            return

        logger.info(
            "Starting search index update: %d package(s), %d service(s) total",
            len(packages),
            len(documents),
        )

        payload = IndexDocumentCollection(documents=documents).model_dump_json().encode("utf-8")
        new_file_id = await self._client.upload_json(payload)

        existing_ids = await self._client.list_vector_store_files(self._vector_store_id)
        stale_ids = [oid for oid in existing_ids if oid != new_file_id]
        if stale_ids:
            logger.info("Removing %d stale file(s) from vector store", len(stale_ids))
            for old_id in stale_ids:
                await self._client.detach_and_delete_file(self._vector_store_id, old_id)

        await self._client.attach_file(self._vector_store_id, new_file_id)
        logger.info(
            "Search index updated successfully: %d documents, file_id=%s",
            len(documents),
            new_file_id,
        )

    @staticmethod
    def _build_document(package: ServicePackage, service: Service) -> IndexedServiceDocument:
        return IndexedServiceDocument(
            provider_id=package.provider.provider_id,
            provider_name=package.provider.name,
            base_platform=package.provider.base_platform,
            service_id=service.service_id,
            category=service.category,
            name=service.name,
            description=service.description,
            pricing_model=service.pricing_model,
            price_from_rub=service.price_from_rub,
            price_unit=service.price_unit,
            compliance_tags=list(service.compliance_tags),
            tech_tags=list(service.tech_tags),
            regions=list(service.regions),
        )
