import math
from dataclasses import dataclass
from typing import List, Tuple, Optional

from src.models import Service
from src.models.service_package import RegionCoord, UserQuery
from src.service.embedder import Embedder
from src.service.repository import ServiceRepository

# --- 1. Конфигурации и структуры ответа ---

@dataclass(frozen=True, slots=True)
class ScoringConfig:
    semantic_weight: float = 0.5
    proximity_weight: float = 0.3
    tag_weight: float = 0.2
    sigma_km: float = 500.0

@dataclass(frozen=True, slots=True)
class ScoreComponents:
    semantic: float
    proximity: float
    tags: float

@dataclass(frozen=True, slots=True)
class ScoredService:
    """Финальный результат скоринга с разбивкой по компонентам."""
    service: Service
    final_score: float
    components: ScoreComponents

@dataclass(slots=True)
class _ScoredCandidate:
    """Внутренняя DTO для промежуточного хранения данных кандидата."""
    service: Service
    embedding: Tuple[float, ...]
    lat: float
    lon: float
    tags: Tuple[str, ...]


# --- 2. Основной сервисный класс ---

class ScoringService:
    """
    Сервис ранжирования: получение эмбеддинга → поиск в БД (pgvector) → гибридный скоринг.
    """
    _DEFAULT_TOP_K = 50

    def __init__(
        self,
        embedder: Embedder,
        repository: ServiceRepository,
        config: Optional[ScoringConfig] = None,
        top_k: int = _DEFAULT_TOP_K,
    ) -> None:
        self._embedder = embedder
        self._repository = repository
        self._config = config or ScoringConfig()
        self._top_k = top_k

    # --- Математика и алгоритмы скоринга ---

    @staticmethod
    def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Вычисляет расстояние между двумя координатами на сфере."""
        radius_km = 6371.0
        lat1_rad, lon1_rad = math.radians(lat1), math.radians(lon1)
        lat2_rad, lon2_rad = math.radians(lat2), math.radians(lon2)

        dlon = lon2_rad - lon1_rad
        dlat = lat2_rad - lat1_rad

        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return radius_km * c

    @staticmethod
    def _semantic_score(query_embedding: Tuple[float, ...], svc_embedding: Tuple[float, ...]) -> float:
        """Косинусное сходство через скалярное произведение, нормализованное к [0, 1]."""
        dot = sum(q * s for q, s in zip(query_embedding, svc_embedding))
        return (dot + 1) / 2

    @staticmethod
    def _proximity_score(distance_km: float, sigma_km: float) -> float:
        """Гауссово затухание для оценки географической близости."""
        return math.exp(-(distance_km**2) / (2 * sigma_km**2))

    @staticmethod
    def _tag_score(required: Tuple[str, ...], service_tags: Tuple[str, ...]) -> float:
        """Доля пересечения требуемых тегов с тегами сервиса."""
        if not required:
            return 1.0
        required_set = {t.lower() for t in required}
        service_set = {t.lower() for t in service_tags}
        return len(required_set & service_set) / len(required_set)

    # --- Внутренние хелперы подготовки данных ---

    def _build_candidates(
        self,
        rows: List[Tuple[Service, List[float], List[RegionCoord]]],
    ) -> List[_ScoredCandidate]:
        """Преобразует сырые строки из репозитория в удобные DTO для скоринга."""
        candidates: List[_ScoredCandidate] = []
        for service, embedding, region_coords in rows:
            tags = tuple(service.compliance_tags + service.tech_tags)

            if region_coords:
                avg_lat = sum(rc.lat for rc in region_coords) / len(region_coords)
                avg_lon = sum(rc.lon for rc in region_coords) / len(region_coords)
            else:
                avg_lat = 0.0
                avg_lon = 0.0

            candidates.append(
                _ScoredCandidate(
                    service=service,
                    embedding=tuple(embedding),
                    lat=avg_lat,
                    lon=avg_lon,
                    tags=tags,
                )
            )
        return candidates

    def _score_candidates(
        self,
        query: UserQuery,
        query_embedding: Tuple[float, ...],
        candidates: List[_ScoredCandidate],
    ) -> List[ScoredService]:
        """Применяет скоринговые функции ко всем кандидатам и взвешивает результат."""
        target_lat = query.target_lat
        target_lon = query.target_lon
        required_tags = tuple(query.required_tags)

        no_location = (target_lat == 0.0 and target_lon == 0.0)
        results: List[ScoredService] = []

        for cand in candidates:
            # 1. Семантика
            sem = self._semantic_score(query_embedding, cand.embedding)

            # 2. География
            if no_location:
                prox = 1.0
            else:
                dist = self._haversine_distance(target_lat, target_lon, cand.lat, cand.lon)
                prox = self._proximity_score(dist, self._config.sigma_km)

            # 3. Теги
            tag = self._tag_score(required_tags, cand.tags)

            # 4. Взвешенная сумма
            final_score = (
                self._config.semantic_weight * sem
                + self._config.proximity_weight * prox
                + self._config.tag_weight * tag
            )

            components = ScoreComponents(
                semantic=round(sem, 4),
                proximity=round(prox, 4),
                tags=round(tag, 4),
            )

            results.append(
                ScoredService(
                    service=cand.service,
                    final_score=final_score,
                    components=components,
                )
            )
        return results

    # --- Основной публичный метод ---

    async def rank(self, query: UserQuery) -> List[ScoredService]:
        """
        Основной метод сервиса. Вызывается из FastAPI роутера.
        """
        # 1. Получаем вектор запроса (используем только текстовый clean_intent)
        # Примечание: если твой embedder полностью синхронный, убери await
        query_embeddings = await self._embedder.embed_texts([query.clean_intent])
        query_embedding: Tuple[float, ...] = tuple(query_embeddings[0])

        # 2. Получаем кандидатов из БД (pgvector)
        # Если в будущем захочешь добавить pre-фильтрацию по бюджету,
        # можно прокинуть query.max_budget сюда.
        rows = await self._repository.search_by_embedding(
            query_embedding=list(query_embedding),
            top_k=self._top_k,
        )

        # 3. Подготавливаем внутренние DTO
        candidates = self._build_candidates(rows)

        # 4. Проводим детальный скоринг
        scored = self._score_candidates(query, query_embedding, candidates)


        scored.sort(key=lambda x: x.final_score, reverse=True)
        
        return scored