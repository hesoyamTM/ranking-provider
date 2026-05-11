"""Таксономия required-полей по тегам компонентов.

ExtractionAgent после извлечения компонентов проходит по их `required_tags`
и собирает список `missing` — обязательных полей, которые надо
доспросить у пользователя на этапе Clarification.

Если ни один из требуемых тегов не покрывает поле — оно не считается
обязательным. Пересечения (compute и docker → vCPU всё равно нужно)
обрабатываются естественно через объединение требований.
"""

from __future__ import annotations

REQUIRED_FIELDS_BY_TAG: dict[str, list[str]] = {
    # Compute / VPS / VDS
    "compute": ["vcpu", "ram", "region"],
    "vds": ["vcpu", "ram", "region"],
    "vps": ["vcpu", "ram", "region"],
    "vm": ["vcpu", "ram", "region"],
    # Managed databases
    "managed_db": ["engine", "size_gb", "region"],
    "postgresql": ["size_gb", "region"],
    "mysql": ["size_gb", "region"],
    "mongodb": ["size_gb", "region"],
    # Object storage / files
    "object_storage": ["expected_volume_gb", "region"],
    "s3": ["expected_volume_gb", "region"],
    # Cache
    "cache": ["size_gb", "region"],
    "redis": ["size_gb", "region"],
    # CDN
    "cdn": ["expected_traffic_tb"],
    # Kubernetes
    "kubernetes": ["nodes_count", "region"],
    "k8s": ["nodes_count", "region"],
    # GPU / ML
    "gpu": ["gpu_type", "vram_gb", "region"],
    "ml": ["gpu_type", "vram_gb", "region"],
    # Load balancer / networking
    "load_balancer": ["region"],
}


# Человеко-читаемые подсказки для ClarificationAgent.
FIELD_HINTS: dict[str, str] = {
    "vcpu": "сколько vCPU нужно",
    "ram": "сколько RAM (ГБ)",
    "region": "регион размещения (Москва, Санкт-Петербург, eu-west и т.п.)",
    "engine": "какой движок СУБД (PostgreSQL, MySQL, MongoDB)",
    "size_gb": "примерный объём данных в ГБ",
    "expected_volume_gb": "ожидаемый объём хранения в ГБ",
    "expected_traffic_tb": "ожидаемый трафик в ТБ/мес",
    "nodes_count": "сколько узлов в кластере",
    "gpu_type": "тип GPU (A100, V100, T4 и т.п.)",
    "vram_gb": "сколько VRAM (ГБ) на одну карту",
    "budget": "какой бюджет в рублях за месяц",
}


def required_fields_for(tags: list[str]) -> list[str]:
    """Объединение required-полей по всем тегам компонента."""
    seen: set[str] = set()
    ordered: list[str] = []
    for tag in tags:
        for field in REQUIRED_FIELDS_BY_TAG.get(tag.lower(), []):
            if field not in seen:
                seen.add(field)
                ordered.append(field)
    return ordered


def hint_for(field: str) -> str:
    """Подсказка для пользователя — что именно ждёт агент в этом поле."""
    return FIELD_HINTS.get(field, field)


# Быстрые варианты ответа для каждого поля — показываются как кнопки-подсказки.
FIELD_SUGGESTIONS: dict[str, list[str]] = {
    "vcpu": ["2 vCPU", "4 vCPU", "8 vCPU", "16 vCPU"],
    "ram": ["4 GB RAM", "8 GB RAM", "16 GB RAM", "32 GB RAM"],
    "region": ["Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург"],
    "engine": ["PostgreSQL", "MySQL", "MongoDB", "ClickHouse"],
    "size_gb": ["20 ГБ", "50 ГБ", "100 ГБ", "500 ГБ"],
    "expected_volume_gb": ["100 ГБ", "500 ГБ", "1000 ГБ", "5000 ГБ"],
    "expected_traffic_tb": ["1 ТБ", "5 ТБ", "10 ТБ", "50 ТБ"],
    "nodes_count": ["1 узел", "3 узла", "5 узлов", "10 узлов"],
    "gpu_type": ["T4", "A10", "A100", "V100"],
    "vram_gb": ["16 ГБ", "24 ГБ", "40 ГБ", "80 ГБ"],
    "budget": ["5 000 ₽/мес", "15 000 ₽/мес", "50 000 ₽/мес", "100 000 ₽/мес"],
}


def suggestions_for_missing(missing_fields: list[str]) -> list[str]:
    """Набор быстрых вариантов ответа для первого missing-поля (показывается как чипы)."""
    for field in missing_fields:
        opts = FIELD_SUGGESTIONS.get(field)
        if opts:
            return opts
    return []


def suggestions_per_field(missing_fields: list[str]) -> list[dict]:
    """Группы вариантов ответа — по одной на каждый missing-вопрос.

    Возвращает список объектов вида:
        {"field": "vcpu", "question": "сколько vCPU нужно", "suggestions": [...]}.
    Поля, для которых нет ни подсказки, ни вариантов, пропускаются.
    """
    groups: list[dict] = []
    for field in missing_fields:
        opts = FIELD_SUGGESTIONS.get(field)
        if not opts:
            continue
        groups.append(
            {
                "field": field,
                "question": hint_for(field),
                "suggestions": opts,
            }
        )
    return groups
