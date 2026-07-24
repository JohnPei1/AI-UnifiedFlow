from collections.abc import Callable

import pytest

from app.normalization.mapping_engine import (
    MappingEngine,
    MappingOperationError,
    MissingSourceFieldError,
    NormalizedEventValidationError,
)
from app.schemas.mappings import RuntimeMapping
from app.schemas.requests import InternalUsageEvent


def test_copy_operation(
    usage_event: InternalUsageEvent,
    mapping_factory: Callable[..., RuntimeMapping],
) -> None:
    result = MappingEngine().apply_mapping(
        usage_event,
        mapping_factory(),
    )

    assert result["resource"] == "i-123"


def test_nested_copy(
    usage_event: InternalUsageEvent,
    mapping_factory: Callable[..., RuntimeMapping],
) -> None:
    mapping = mapping_factory(
        fields={
            "quantity": [
                {"operation": "copy", "from": "payload.nested.value"},
                {"operation": "cast", "to": "float"},
            ]
        }
    )

    result = MappingEngine().apply_mapping(usage_event, mapping)

    assert result["quantity"] == 2.0


def test_copy_from_source(
    usage_event: InternalUsageEvent,
    mapping_factory: Callable[..., RuntimeMapping],
) -> None:
    mapping = mapping_factory(
        fields={
            "resource": [{"operation": "copy", "from": "source"}],
        }
    )

    result = MappingEngine().apply_mapping(usage_event, mapping)

    assert result["resource"] == "aws"


@pytest.mark.parametrize(
    ("source_path", "target_type", "expected"),
    [
        ("payload.usage_amount", "float", 3.5),
        ("payload.nested.value", "integer", 2),
    ],
)
def test_cast_operation(
    usage_event: InternalUsageEvent,
    mapping_factory: Callable[..., RuntimeMapping],
    source_path: str,
    target_type: str,
    expected: object,
) -> None:
    mapping = mapping_factory(
        fields={
            "quantity": [
                {"operation": "copy", "from": source_path},
                {"operation": "cast", "to": target_type},
            ]
        }
    )

    result = MappingEngine().apply_mapping(usage_event, mapping)

    assert result["quantity"] == expected


def test_multiply_operation(
    usage_event: InternalUsageEvent,
    mapping_factory: Callable[..., RuntimeMapping],
) -> None:
    mapping = mapping_factory(
        fields={
            "quantity": [
                {"operation": "copy", "from": "payload.usage_amount"},
                {"operation": "cast", "to": "float"},
                {"operation": "multiply", "by": 60},
            ]
        }
    )

    result = MappingEngine().apply_mapping(usage_event, mapping)

    assert result["quantity"] == 210.0


def test_operation_order_is_preserved(
    usage_event: InternalUsageEvent,
    mapping_factory: Callable[..., RuntimeMapping],
) -> None:
    mapping = mapping_factory(
        fields={
            "quantity": [
                {"operation": "copy", "from": "payload.nested.value"},
                {"operation": "cast", "to": "integer"},
                {"operation": "multiply", "by": 2.5},
            ]
        }
    )

    result = MappingEngine().apply_mapping(usage_event, mapping)

    assert result["quantity"] == 5.0


def test_missing_source_field(
    usage_event: InternalUsageEvent,
    mapping_factory: Callable[..., RuntimeMapping],
) -> None:
    mapping = mapping_factory(
        fields={
            "resource": [
                {"operation": "copy", "from": "payload.missing"}
            ]
        }
    )

    with pytest.raises(MissingSourceFieldError, match="operation 1"):
        MappingEngine().apply_mapping(usage_event, mapping)


def test_invalid_cast(
    usage_event: InternalUsageEvent,
    mapping_factory: Callable[..., RuntimeMapping],
) -> None:
    mapping = mapping_factory(
        fields={
            "quantity": [
                {"operation": "copy", "from": "payload.resource_id"},
                {"operation": "cast", "to": "float"},
            ]
        }
    )

    with pytest.raises(MappingOperationError, match="cannot cast"):
        MappingEngine().apply_mapping(usage_event, mapping)


def test_invalid_multiply(
    usage_event: InternalUsageEvent,
    mapping_factory: Callable[..., RuntimeMapping],
) -> None:
    mapping = mapping_factory(
        fields={
            "quantity": [
                {"operation": "copy", "from": "payload.resource_id"},
                {"operation": "multiply", "by": 2},
            ]
        }
    )

    with pytest.raises(MappingOperationError, match="requires a numeric value"):
        MappingEngine().apply_mapping(usage_event, mapping)


def test_normalized_schema_failure(
    usage_event: InternalUsageEvent,
    mapping_factory: Callable[..., RuntimeMapping],
) -> None:
    mapping = mapping_factory(
        static_fields={
            "category": "invalid",
            "usage_type": "compute_time",
        }
    )

    with pytest.raises(
        NormalizedEventValidationError,
        match="category",
    ):
        MappingEngine().apply_mapping(usage_event, mapping)


def test_protected_mapping_target_is_rejected(
    usage_event: InternalUsageEvent,
    mapping_factory: Callable[..., RuntimeMapping],
) -> None:
    mapping = mapping_factory(
        fields={
            "event_id": [
                {"operation": "copy", "from": "payload.resource_id"}
            ]
        }
    )

    with pytest.raises(MappingOperationError, match="protected fields"):
        MappingEngine().apply_mapping(usage_event, mapping)
