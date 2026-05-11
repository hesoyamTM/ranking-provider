from __future__ import annotations

import math
from typing import Any

from src.service.scorer import ScoredService

MIN_ACCEPTABLE_SCORE = 0.35
GOOD_TOP_SCORE = 0.55
TOP_K_RETURN = 15


def monthly_price(price_from_rub: float, price_unit: str) -> float:
    unit = (price_unit or "").lower()
    if any(k in unit for k in ("month", "мес")):
        return price_from_rub
    if any(k in unit for k in ("hour", "час")):
        return price_from_rub * 24 * 30
    if any(k in unit for k in ("minute", "минут")):
        return price_from_rub * 60 * 24 * 30
    if "second" in unit or "секунд" in unit:
        return price_from_rub * 60 * 60 * 24 * 30
    if any(k in unit for k in ("day", "сут", "ден")):
        return price_from_rub * 30
    if any(k in unit for k in ("year", "год")):
        return price_from_rub / 12
    return price_from_rub


def refinement_hint(
    kept: list[ScoredService],
    top_score: float,
    tag_coverage: float,
    required_tags: list[str],
) -> str:
    if not kept:
        return (
            "Под фильтры (бюджет/score) не прошла ни одна услуга. "
            "Сформулируй clean_intent более конкретно или ослабь набор тегов."
        )
    if top_score < GOOD_TOP_SCORE:
        return (
            f"Низкое смысловое совпадение (top_final_score={top_score:.2f}). "
            "Перепиши clean_intent более развёрнуто и точнее по сценарию."
        )
    if required_tags and tag_coverage < 0.4:
        return (
            "Большинство кандидатов не имеют требуемых тегов. "
            "Проверь required_tags: возможно, теги слишком узкие или "
            "написаны не как у провайдеров (например 'k8s' вместо 'kubernetes')."
        )
    return ""


def validate_and_filter(
    ranked: list[ScoredService],
    arguments: dict[str, Any],
) -> tuple[list[ScoredService], dict[str, Any]]:
    """Фильтрует результаты скорера и возвращает (отфильтрованный список, метрики качества)."""
    max_budget = arguments.get("max_budget")
    required_tags = [t.lower() for t in (arguments.get("required_tags") or []) if t]

    total = len(ranked)
    kept: list[ScoredService] = []
    dropped_budget = 0
    dropped_score = 0
    dropped_invalid = 0

    for item in ranked:
        score = item.score.final_score
        comp = item.score

        if not (
            math.isfinite(score)
            and math.isfinite(comp.semantic)
            and math.isfinite(comp.proximity)
            and math.isfinite(comp.tags)
        ):
            dropped_invalid += 1
            continue

        if max_budget is not None:
            est = monthly_price(float(item.service.price_from_rub), item.service.price_unit)
            if est > float(max_budget):
                dropped_budget += 1
                continue

        if score < MIN_ACCEPTABLE_SCORE:
            dropped_score += 1
            continue

        kept.append(item)

    kept = kept[:TOP_K_RETURN]

    top_score = kept[0].score.final_score if kept else 0.0
    avg_score = sum(it.score.final_score for it in kept) / len(kept) if kept else 0.0

    if required_tags and kept:
        tag_coverage = sum(1 for it in kept if it.score.tags > 0.0) / len(kept)
    else:
        tag_coverage = 1.0

    needs = not kept or top_score < GOOD_TOP_SCORE or (bool(required_tags) and tag_coverage < 0.4)

    quality: dict[str, Any] = {
        "candidates_total": total,
        "candidates_kept": len(kept),
        "dropped_by_budget": dropped_budget,
        "dropped_by_low_score": dropped_score,
        "dropped_invalid": dropped_invalid,
        "top_final_score": round(top_score, 4),
        "avg_final_score": round(avg_score, 4),
        "tag_coverage": round(tag_coverage, 4),
        "needs_refinement": needs,
        "refinement_hint": refinement_hint(kept, top_score, tag_coverage, required_tags) if needs else "",
    }
    return kept, quality
