from collections.abc import Callable

import pytest

from app.schemas.mappings import RuntimeMapping
from app.schemas.requests import InternalUsageEvent


@pytest.fixture
def usage_event() -> InternalUsageEvent:
    return InternalUsageEvent(
        event_id="evt_123",
        case_id="case_123",
        schema_fingerprint="schema_123",
        source="aws",
        payload={
            "resource_id": "i-123",
            "usage_amount": "3.5",
            "usage_unit": "hours",
            "usage_started_at": "2026-07-22T14:00:00Z",
            "nested": {"value": "2"},
        },
    )


@pytest.fixture
def mapping_factory() -> Callable[..., RuntimeMapping]:
    def create_mapping(
        fields: dict[str, list[dict[str, object]]] | None = None,
        static_fields: dict[str, object] | None = None,
        case_id: str = "case_123",
        source: str = "aws",
        created_by: str = "user",
    ) -> RuntimeMapping:
        return RuntimeMapping(
            case_id=case_id,
            source=source,
            schema_fingerprint="schema_123",
            version=1,
            created_by=created_by,
            static_fields=static_fields
            or {
                "category": "cloud",
                "service": "ec2",
                "usage_type": "compute_time",
            },
            fields=fields
            or {
                "resource": [
                    {"operation": "copy", "from": "payload.resource_id"}
                ]
            },
        )

    return create_mapping
