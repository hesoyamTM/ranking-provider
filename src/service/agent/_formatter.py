from __future__ import annotations

import json
from typing import Any

from src.service.scorer import ScoredService
from src.service.agent._filter import monthly_price

TOP_PROVIDERS = 3
TOP_SERVICES_PER_PROVIDER = 3

PROVIDER_PREFIXES: dict[str, str] = {
    "yandexcloud": "Yandex Cloud",
    "selectel": "Selectel",
}


def infer_provider(service_id: str) -> str:
    """Определяет имя провайдера из service_id по известным префиксам."""
    lower = service_id.lower()
    for prefix, name in PROVIDER_PREFIXES.items():
        if lower.startswith(prefix):
            return name
    return service_id.split("_")[0]


def service_to_dict(item: ScoredService) -> dict[str, Any]:
    svc = item.service
    price = float(svc.price_from_rub)
    return {
        "name": svc.name,
        "category": svc.category,
        "description": svc.description,
        "pricing_model": svc.pricing_model,
        "price_from_rub": round(price, 2),
        "price_unit": svc.price_unit,
        "monthly_price_rub": round(monthly_price(price, svc.price_unit), 2),
        "regions": svc.regions,
        "_internal_scores": {
            "final": round(item.score.final_score, 4),
            "semantic": round(item.score.semantic, 4),
            "proximity": round(item.score.proximity, 4),
            "tags": round(item.score.tags, 4),
        },
        "_internal_tags": {
            "tech": svc.tech_tags,
            "compliance": svc.compliance_tags,
        },
    }


def group_by_provider(items: list[ScoredService]) -> list[dict[str, Any]]:
    """Группирует услуги по провайдеру, ранжирует по лучшему score, возвращает топ-N."""
    groups: dict[str, list[ScoredService]] = {}
    for item in items:
        groups.setdefault(infer_provider(item.service.service_id), []).append(item)

    ranked = sorted(
        groups.items(),
        key=lambda kv: max(s.score.final_score for s in kv[1]),
        reverse=True,
    )

    result: list[dict[str, Any]] = []
    for provider_name, svcs in ranked[:TOP_PROVIDERS]:
        best = sorted(svcs, key=lambda s: s.score.final_score, reverse=True)
        result.append(
            {
                "provider": provider_name,
                "_internal_top_score": round(best[0].score.final_score, 4),
                "services": [service_to_dict(s) for s in best[:TOP_SERVICES_PER_PROVIDER]],
            }
        )
    return result


def group_plan_by_provider(
    component_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Группирует результаты plan_system по провайдеру.

    Провайдеры ранжируются по покрытию (число компонентов), затем по среднему top-score.
    """
    provider_map: dict[str, dict[str, list[ScoredService]]] = {}

    for cr in component_results:
        comp_name = cr["name"]
        for item in cr["filtered"]:
            provider = infer_provider(item.service.service_id)
            provider_map.setdefault(provider, {}).setdefault(comp_name, []).append(item)

    for comp_map in provider_map.values():
        for name in comp_map:
            comp_map[name].sort(key=lambda s: s.score.final_score, reverse=True)

    def _rank_key(kv: tuple[str, dict[str, list[ScoredService]]]) -> tuple[int, float]:
        _, comp_map = kv
        coverage = len(comp_map)
        avg_top = sum(svcs[0].score.final_score for svcs in comp_map.values() if svcs) / max(coverage, 1)
        return (coverage, avg_top)

    ranked = sorted(provider_map.items(), key=_rank_key, reverse=True)

    result: list[dict[str, Any]] = []
    total_components = len(component_results)
    for provider_name, comp_map in ranked[:TOP_PROVIDERS]:
        coverage = len(comp_map)
        avg_top = sum(svcs[0].score.final_score for svcs in comp_map.values() if svcs) / max(coverage, 1)
        result.append(
            {
                "provider": provider_name,
                "components_covered": coverage,
                "total_components": total_components,
                "_internal_avg_top_score": round(avg_top, 4),
                "components": [
                    {
                        "component": comp_name,
                        "services": [service_to_dict(s) for s in svcs[:TOP_SERVICES_PER_PROVIDER]],
                    }
                    for comp_name, svcs in comp_map.items()
                ],
            }
        )
    return result


def format_for_llm(
    filtered: list[ScoredService],
    quality: dict[str, Any],
    arguments: dict[str, Any],
) -> str:
    """Собирает JSON-payload для LLM после rank_services."""
    payload = {
        "enriched_query": {
            "clean_intent": arguments.get("clean_intent", ""),
            "location_name": arguments.get("location_name") or "",
            "max_budget_rub": arguments.get("max_budget"),
        },
        "_internal_quality": quality,
        "providers_ranked": group_by_provider(filtered),
        "instructions_for_assistant": (
            "Поля _internal_* — только для внутреннего анализа. "
            "НИКОГДА не показывай их значения пользователю: ни score, ни теги, ни ключи JSON.\n"
            "Если _internal_quality.needs_refinement=true и есть попытка — "
            "вызови rank_services снова с улучшенными параметрами.\n"
            "Иначе сформируй ответ строго по формату системного промпта: "
            "ТОП провайдеров → для каждого почему + лучшие тарифы "
            "(name, description, monthly_price_rub, regions) → итоговая рекомендация. "
            "Используй только поля без _ в названии для вывода."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def format_plan_for_llm(
    component_results: list[dict[str, Any]],
    arguments: dict[str, Any],
) -> str:
    """Собирает JSON-payload для LLM после plan_system."""
    payload = {
        "query_summary": {
            "location_name": arguments.get("location_name") or "",
            "max_budget_total_rub": arguments.get("max_budget_total_rub"),
            "components_count": len(component_results),
        },
        "components_overview": [
            {
                "name": cr["name"],
                "clean_intent": cr["clean_intent"],
                "_internal_quality": cr["quality"],
            }
            for cr in component_results
        ],
        "providers_ranked": group_plan_by_provider(component_results),
        "instructions_for_assistant": (
            "Поля _internal_* — только для внутреннего анализа. "
            "НИКОГДА не показывай их значения пользователю.\n"
            "Сформируй ответ по формату СИСТЕМНОГО ЗАПРОСА из системного промпта:\n"
            "1. Что понял + список компонентов из components_overview.\n"
            "2. ТОП провайдеров из providers_ranked (лучший — первый): "
            "для каждого — почему он подходит + тарифы по компонентам "
            "(name, description, monthly_price_rub, regions) + итого ₽/мес.\n"
            "3. Итоговая рекомендация.\n"
            "Если components_covered < total_components у провайдера — "
            "честно упомяни, что он закрывает не все компоненты.\n"
            "Если у компонента _internal_quality.needs_refinement=true — "
            "отметь, что по нему данных мало."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
