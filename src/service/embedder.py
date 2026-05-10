from __future__ import annotations

from typing import Protocol

from src.models import Service


class Embedder(Protocol):
    async def embed(self, service: Service) -> list[float]: ...

    async def embed_batch(self, services: list[Service]) -> list[list[float]]: ...
