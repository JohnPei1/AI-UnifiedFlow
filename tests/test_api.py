from collections.abc import Callable, Iterator
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes
from app.messaging.kafka_client import KafkaPublishError
from app.normalization.fingerprint import (
    calculate_case_id,
    calculate_schema_fingerprint,
)
from app.schemas.mappings import RuntimeMapping


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(routes.router)
    app.state.kafka_producer = object()
    app.state.database_engine = object()
    app.state.mapping_store = Mock()
    app.state.supported_sources = frozenset({"aws", "openai"})

    with TestClient(app) as client:
        yield client


def test_valid_event_is_published(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish = Mock()
    monkeypatch.setattr(routes, "publish_event", publish)

    response = api_client.post(
        "/events",
        json={"source": " AWS ", "payload": {"usage_amount": 3.5}},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert response.json()["event_id"].startswith("evt_")
    published_event = publish.call_args.kwargs["event"]
    assert published_event.source == "aws"
    assert published_event.payload == {"usage_amount": 3.5}
    assert publish.call_args.kwargs["topic"] == "raw-events"
    assert "key" not in publish.call_args.kwargs


def test_missing_source_is_rejected(api_client: TestClient) -> None:
    response = api_client.post(
        "/events",
        json={"payload": {"usage_amount": 3.5}},
    )

    assert response.status_code == 422


def test_blank_source_is_rejected(api_client: TestClient) -> None:
    response = api_client.post(
        "/events",
        json={"source": " ", "payload": {"usage_amount": 3.5}},
    )

    assert response.status_code == 422


def test_invalid_payload_is_rejected(api_client: TestClient) -> None:
    response = api_client.post(
        "/events",
        json={"source": "aws", "payload": ["not", "an", "object"]},
    )

    assert response.status_code == 422


def test_unsupported_source_is_rejected(api_client: TestClient) -> None:
    response = api_client.post(
        "/events",
        json={"source": "azure", "payload": {"usage_amount": 3.5}},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "unsupported source: azure"


def test_mapping_found(
    api_client: TestClient,
    mapping_factory: Callable[..., RuntimeMapping],
) -> None:
    payload = {"resource_id": "i-123"}
    fingerprint = calculate_schema_fingerprint(payload)
    case_id = calculate_case_id("aws", fingerprint)
    mapping = mapping_factory(case_id=case_id)
    api_client.app.state.mapping_store.get.return_value = mapping

    response = api_client.post(
        "/mappings/resolve",
        json={"source": "aws", "payload": payload},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "available"
    assert response.json()["mapping"]["case_id"] == case_id


def test_mapping_missing(api_client: TestClient) -> None:
    api_client.app.state.mapping_store.get.return_value = None

    response = api_client.post(
        "/mappings/resolve",
        json={"source": "aws", "payload": {"resource_id": "i-123"}},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "not_found"
    assert response.json()["mapping"] is None


def test_health_endpoint(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "is_kafka_healthy", lambda producer: True)
    monkeypatch.setattr(
        routes,
        "is_database_healthy",
        lambda engine: True,
    )

    response = api_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "kafka": "healthy",
        "postgresql": "healthy",
    }


def test_health_reports_unavailable_dependencies(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "is_kafka_healthy", lambda producer: False)
    monkeypatch.setattr(
        routes,
        "is_database_healthy",
        lambda engine: False,
    )

    response = api_client.get("/health")

    assert response.json() == {
        "kafka": "unhealthy",
        "postgresql": "unhealthy",
    }


def test_kafka_unavailable_returns_service_unavailable(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_publish(*args: object, **kwargs: object) -> None:
        raise KafkaPublishError("unavailable")

    monkeypatch.setattr(routes, "publish_event", fail_publish)

    response = api_client.post(
        "/events",
        json={"source": "aws", "payload": {"usage_amount": 3.5}},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Kafka is unavailable"
