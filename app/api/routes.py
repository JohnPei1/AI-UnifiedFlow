"""Expose usage ingestion, mapping lookup, and health endpoints."""

from uuid import uuid4

from confluent_kafka import Producer
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import Engine

from app.db.database import is_database_healthy
from app.db.repository import NormalizedEventRepository
from app.messaging.kafka_client import (
    RAW_EVENTS_TOPIC,
    KafkaClientError,
    is_kafka_healthy,
    publish_event,
)
from app.normalization.fingerprint import (
    calculate_case_id,
    calculate_schema_fingerprint,
)
from app.normalization.mapping_store import MappingStore
from app.schemas.requests import (
    EventAcceptedResponse,
    HealthResponse,
    InternalUsageEvent,
    MappingResolveRequest,
    MappingResolveResponse,
    StoredNormalizedEvent,
    UsageEventRequest,
)

router = APIRouter()


def get_kafka_producer(request: Request) -> Producer:
    return request.app.state.kafka_producer


def get_mapping_store(request: Request) -> MappingStore:
    return request.app.state.mapping_store


def get_database_engine(request: Request) -> Engine:
    return request.app.state.database_engine


def get_event_repository(request: Request) -> NormalizedEventRepository:
    return request.app.state.event_repository


def get_supported_sources(request: Request) -> frozenset[str]:
    return request.app.state.supported_sources


def _resolve_identifiers(
    source: str,
    payload: dict[str, object],
    supported_sources: frozenset[str],
) -> tuple[str, str]:
    if source not in supported_sources:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unsupported source: {source}",
        )

    schema_fingerprint = calculate_schema_fingerprint(payload)
    case_id = calculate_case_id(source, schema_fingerprint)
    return schema_fingerprint, case_id


@router.post(
    "/events",
    response_model=EventAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_event(
    event_request: UsageEventRequest,
    producer: Producer = Depends(get_kafka_producer),
    supported_sources: frozenset[str] = Depends(get_supported_sources),
) -> EventAcceptedResponse:
    """Validate and publish an internal usage event."""

    schema_fingerprint, case_id = _resolve_identifiers(
        source=event_request.source,
        payload=event_request.payload,
        supported_sources=supported_sources,
    )
    event_id = f"evt_{uuid4().hex}"
    event = InternalUsageEvent(
        event_id=event_id,
        case_id=case_id,
        schema_fingerprint=schema_fingerprint,
        source=event_request.source,
        payload=event_request.payload,
    )

    try:
        publish_event(
            producer,
            topic=RAW_EVENTS_TOPIC,
            event=event,
        )
    except KafkaClientError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kafka is unavailable",
        ) from error

    return EventAcceptedResponse(
        event_id=event_id,
        case_id=case_id,
    )


@router.get(
    "/test/get-all-events",
    response_model=list[StoredNormalizedEvent],
)
def get_test_events(
    repository: NormalizedEventRepository = Depends(get_event_repository),
) -> list[StoredNormalizedEvent]:
    return [
        StoredNormalizedEvent.model_validate(record)
        for record in repository.get_all()
    ]


@router.post(
    "/mappings/resolve",
    response_model=MappingResolveResponse,
)
def resolve_mapping(
    request: MappingResolveRequest,
    mapping_store: MappingStore = Depends(get_mapping_store),
    supported_sources: frozenset[str] = Depends(get_supported_sources),
) -> MappingResolveResponse:
    """Return an existing mapping without creating one."""

    _, case_id = _resolve_identifiers(
        source=request.source,
        payload=request.payload,
        supported_sources=supported_sources,
    )
    mapping = mapping_store.get(case_id)
    if mapping is None:
        return MappingResolveResponse(
            case_id=case_id,
            status="not_found",
            mapping=None,
        )

    return MappingResolveResponse(
        case_id=case_id,
        status="available",
        mapping=mapping,
    )


@router.get(
    "/health",
    response_model=HealthResponse,
)
def health(
    producer: Producer = Depends(get_kafka_producer),
    database_engine: Engine = Depends(get_database_engine),
) -> HealthResponse:
    """Report Kafka and PostgreSQL connectivity."""

    kafka_healthy = is_kafka_healthy(producer)
    postgresql_healthy = is_database_healthy(database_engine)
    return HealthResponse(
        kafka="healthy" if kafka_healthy else "unhealthy",
        postgresql="healthy" if postgresql_healthy else "unhealthy",
    )
