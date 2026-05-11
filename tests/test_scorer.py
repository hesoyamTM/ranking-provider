import json

import pytest

from src.service.scorer import RAGScoringService

SAMPLE_RESPONSE = {
    "technology_name": "Kubernetes",
    "providers": [
        {
            "provider_name": "t1-cloud",
            "top_services": [
                {
                    "service_id": "svc-001",
                    "service_name": "Managed Kubernetes",
                    "description": "Fully managed K8s cluster",
                    "category": "Containers",
                    "pricing_model": "pay-as-you-go",
                    "price": "5.00",
                    "pricing_unit": "vCPU/hour",
                    "tech_stack": ["kubernetes", "docker"],
                    "compliance": ["ISO27001"],
                    "justification": "Best fit for your workload",
                }
            ],
        },
        {
            "provider_name": "yandex-cloud",
            "top_services": [
                {
                    "service_id": "svc-002",
                    "service_name": "Yandex Managed K8s",
                    "description": "K8s on Yandex Cloud",
                    "category": "Containers",
                    "pricing_model": "hourly",
                    "price": "3.50",
                    "pricing_unit": "node/hour",
                    "tech_stack": ["kubernetes"],
                    "compliance": [],
                    "justification": "Cost-effective option",
                }
            ],
        },
    ],
}


def test_parse_response_returns_all_providers():
    result = RAGScoringService._parse_response(json.dumps(SAMPLE_RESPONSE))
    assert set(result.keys()) == {"t1-cloud", "yandex-cloud"}


def test_parse_response_service_fields():
    result = RAGScoringService._parse_response(json.dumps(SAMPLE_RESPONSE))
    svc = result["t1-cloud"][0]
    assert svc.provider_id == "t1-cloud"
    assert svc.service_id == "svc-001"
    assert svc.name == "Managed Kubernetes"
    assert svc.description == "Fully managed K8s cluster"
    assert svc.category == "Containers"
    assert svc.pricing_model == "pay-as-you-go"
    assert svc.price_unit == "vCPU/hour"
    assert svc.tech_tags == ["kubernetes", "docker"]
    assert svc.compliance_tags == ["ISO27001"]


def test_parse_response_service_count():
    result = RAGScoringService._parse_response(json.dumps(SAMPLE_RESPONSE))
    assert len(result["t1-cloud"]) == 1
    assert len(result["yandex-cloud"]) == 1


def test_parse_response_empty_providers():
    raw = json.dumps({"technology_name": "X", "providers": []})
    result = RAGScoringService._parse_response(raw)
    assert result == {}


def test_parse_response_missing_optional_fields():
    minimal = {
        "providers": [
            {
                "provider_name": "test-provider",
                "top_services": [
                    {"service_id": "s1", "service_name": "Basic Service"}
                ],
            }
        ]
    }
    result = RAGScoringService._parse_response(json.dumps(minimal))
    svc = result["test-provider"][0]
    assert svc.service_id == "s1"
    assert svc.name == "Basic Service"
    assert svc.tech_tags == []
    assert svc.compliance_tags == []
