from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Tuple

from src.models import Service
from src.models.scoring_package import ScoredService, ServiceDTO, Score
from src.models.service_package import UserQuery
from src.service.embedder import Embedder
from src.service.repository import ServiceRepository


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    semantic_weight: float = 0.8
    tag_weight: float = 0.2


class ScoringService:
    def __init__(
        self,
        embedder: Embedder,
        repository: ServiceRepository,
        config: Optional[ScoringConfig] = None,
    ) -> None:
        self._embedder = embedder
        self._repository = repository
        self._config = config or ScoringConfig()

    @staticmethod
    def _tag_score(required: Tuple[str, ...], service_tags: Tuple[str, ...]) -> float:
        if not required:
            return 1.0
        required_set = {t.lower() for t in required}
        service_set = {t.lower() for t in service_tags}
        return len(required_set & service_set) / len(required_set)

    async def _rank(
        self,
        query: UserQuery,
        fetch_rows: Callable[[List[float]], Awaitable[List[ServiceDTO]]],
    ) -> List[ScoredService]:
        query_embeddings = await self._embedder.embed_texts([query.clean_intent])
        query_embedding = list(query_embeddings[0])
        rows = await fetch_rows(query_embedding)

        required_tags = tuple(query.required_tags)
        scored: List[ScoredService] = []
        for r in rows:
            tag_score = self._tag_score(
                required_tags,
                tuple(r.tech_tags) + tuple(r.compliance_tags),
            )
            score: Score = Score(
                semantic=r.score,
                tags=tag_score,
                final_score=tag_score * self._config.tag_weight + r.score * self._config.semantic_weight
            )
            scored.append(
                ScoredService(
                    service=Service(
                        provider_id=r.provider_id,
                        service_id=r.service_id,
                        category=r.category,
                        name=r.name,
                        description=r.description,
                        pricing_model=r.pricing_model,
                        price_from_rub=r.price_from_rub,
                        price_unit=r.price_unit,
                        tech_tags=r.tech_tags,
                        compliance_tags=r.compliance_tags,
                    ),
                    score=score,
                )
            )
        scored.sort(key=lambda x: x.score.final_score, reverse=True)
        return scored

    async def rank_by_provider(self, query: UserQuery) -> List[ScoredService]:
        return await self._rank(
            query,
            lambda emb: self._repository.search_by_embedding_all_providers(
                query_embedding=emb,
            ),
        )

    async def rank_top_services(self, query: UserQuery) -> List[ScoredService]:
        return await self._rank(
            query,
            lambda emb: self._repository.search_by_embedding_top_services(
                query_embedding=emb,
            ),
        )