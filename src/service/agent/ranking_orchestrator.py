from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.models.service_package import UserQuery
from src.models.session import ComponentSpec, SessionState
from src.service.agent._filter import validate_and_filter
from src.service.agent._formatter import group_by_provider, group_plan_by_provider
from src.service.agent.protocols import Geocoder, RelevanceScorer
from src.service.scorer import ScoredService

logger = logging.getLogger(__name__)


class RankingOrchestrator:
    """Императивный fan-out: параллельно ранжирует каждый компонент.

    Не использует LLM — это чистая функция от подтверждённого
    `SessionState`, ради детерминизма и экономии токенов.
    """

    def __init__(self, scorer: RelevanceScorer, geocoder: Geocoder) -> None:
        self._scorer = scorer
        self._geocoder = geocoder

    async def rank(self, state: SessionState) -> SessionState:
        components = state.components
        if not components:
            logger.warning("RankingOrchestrator: пустой список компонентов")
            new_state = state.model_copy(deep=True)
            new_state.rankings = {"components": [], "providers_ranked": []}
            return new_state

        # Геокодинг общего региона делаем один раз.
        target_lat, target_lon = 0.0, 0.0
        if state.location_name:
            coords = await self._geocoder.geocode(state.location_name)
            if coords is not None:
                target_lat, target_lon = coords
                logger.info(
                    "Geocoded %r -> %.6f, %.6f",
                    state.location_name, target_lat, target_lon,
                )
            else:
                logger.warning("Geocoder returned nothing for %r", state.location_name)

        budget_per_component = self._budget_per_component(state)

        ranked_pairs = await asyncio.gather(
            *[
                self._rank_one(comp, state.location_name, target_lat, target_lon,
                               budget_per_component)
                for comp in components
            ]
        )

        component_payloads: list[dict[str, Any]] = []
        component_results_for_grouping: list[dict[str, Any]] = []
        for comp, (services_ranked, providers_ranked) in zip(components, ranked_pairs):
            arguments = {
                "required_tags": comp.required_tags,
                "max_budget": comp.max_budget_rub if comp.max_budget_rub is not None
                              else budget_per_component,
            }
            filtered_services, quality = validate_and_filter(services_ranked, arguments)
            filtered_providers, _ = validate_and_filter(providers_ranked, arguments)
            logger.info(
                "Component %r: kept=%d top=%.4f needs_refinement=%s",
                comp.name, quality["candidates_kept"],
                quality["top_final_score"], quality["needs_refinement"],
            )
            component_payloads.append(
                {
                    "name": comp.name,
                    "clean_intent": comp.clean_intent,
                    "providers_ranked": group_by_provider(filtered_services),
                    "_internal_quality": quality,
                }
            )
            component_results_for_grouping.append(
                {"name": comp.name, "filtered": filtered_providers, "quality": quality}
            )

        new_state = state.model_copy(deep=True)
        new_state.rankings = {
            "query_summary": {
                "location_name": state.location_name,
                "max_budget_total_rub": state.max_budget_total_rub,
                "components_count": len(components),
            },
            "components": component_payloads,
            "providers_ranked": group_plan_by_provider(component_results_for_grouping),
        }
        return new_state

    @staticmethod
    def _budget_per_component(state: SessionState) -> float | None:
        if state.max_budget_total_rub and state.components:
            return state.max_budget_total_rub / len(state.components)
        return None

    async def _rank_one(
        self,
        component: ComponentSpec,
        global_location: str,
        target_lat: float,
        target_lon: float,
        global_budget_per_component: float | None,
    ) -> tuple[list[ScoredService], list[ScoredService]]:
        # Если у компонента есть свой регион — геокодируем отдельно.
        comp_lat, comp_lon = target_lat, target_lon
        if component.location_name and component.location_name != global_location:
            coords = await self._geocoder.geocode(component.location_name)
            if coords is not None:
                comp_lat, comp_lon = coords

        budget = component.max_budget_rub
        if budget is None:
            budget = global_budget_per_component

        query = UserQuery(
            clean_intent=component.clean_intent,
            required_tags=component.required_tags,
            max_budget=budget,
            location_name=component.location_name or global_location,
            target_lat=comp_lat,
            target_lon=comp_lon,
        )
        services_ranked, providers_ranked = await asyncio.gather(
            self._scorer.rank_by_services(query=query),
            self._scorer.rank_by_providers(query=query),
        )
        return services_ranked, providers_ranked
