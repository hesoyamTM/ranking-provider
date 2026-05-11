from __future__ import annotations


from src.models import Service
from src.models.scoring_package import ScoredService
from src.models.service_package import UserQuery
from src.service.protocols import RAGService

__all__ = ["ScoringService", "ScoredService"]


class ScoringService:
    def __init__(self, rag: RAGService) -> None:
        self._rag = rag

    @staticmethod
    def _parse_response(raw: str) -> str:
        return raw

    async def rank_by_rag(self, user_query: UserQuery) -> str:
        raw = await self._rag.ask(user_query.clean_intent)
        return self._parse_response(raw)
