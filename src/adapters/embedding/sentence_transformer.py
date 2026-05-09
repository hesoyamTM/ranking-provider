from __future__ import annotations

import asyncio
from functools import partial

from sentence_transformers import SentenceTransformer

from src.models import Service


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self._model = SentenceTransformer(model_name)

    @staticmethod
    def _to_text(service: Service) -> str:
        parts = [
            service.name,
            service.category,
            service.description,
            "tech: " + ", ".join(service.tech_tags),
            "compliance: " + ", ".join(service.compliance_tags),
        ]
        return "\n".join(p for p in parts if p)

    def _encode_one(self, service: Service) -> list[float]:
        vec = self._model.encode(self._to_text(service), normalize_embeddings=True)
        return vec.tolist()

    def _encode_batch(self, services: list[Service]) -> list[list[float]]:
        texts = [self._to_text(s) for s in services]
        vectors = self._model.encode(texts, normalize_embeddings=True, batch_size=16)
        return [v.tolist() for v in vectors]

    async def embed(self, service: Service) -> list[float]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._encode_one, service)

    async def embed_batch(self, services: list[Service]) -> list[list[float]]:
        if not services:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self._encode_batch, services))

    @property
    def dimension(self) -> int:
        return int(self._model.get_sentence_embedding_dimension())
