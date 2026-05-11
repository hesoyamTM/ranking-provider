from typing import Protocol


class RAGService(Protocol):
    async def ask(self, query: str) -> str: ...
