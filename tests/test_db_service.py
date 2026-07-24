from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Iterator

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError

from app.db.service import (
    NormalizedEventRepository,
    NormalizedEventStorageError,
)


class FakeSession:
    def __init__(
        self,
        rowcount: int = 1,
        error: Exception | None = None,
    ) -> None:
        self.rowcount = rowcount
        self.error = error
        self.params: dict[str, object] | None = None

    def execute(self, statement: object):
        if self.error is not None:
            raise self.error
        compiled = statement.compile(dialect=postgresql.dialect())
        self.params = compiled.params
        return type("Result", (), {"rowcount": self.rowcount})()


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    @contextmanager
    def begin(self) -> Iterator[FakeSession]:
        yield self.session


def normalized_event() -> dict[str, object]:
    return {
        "event_id": "evt_1",
        "case_id": "case_1",
        "source": "aws",
        "category": "cloud",
        "service": "ec2",
        "resource": "i-123",
        "account_id": "account-123",
        "project_id": "project-456",
        "region": "us-east-1",
        "usage_type": "compute_time",
        "input_units": None,
        "output_units": None,
        "quantity": 3.5,
        "unit": "hours",
        "cost": 0.42,
        "currency": "USD",
        "usage_start": "2026-07-22T14:00:00Z",
        "usage_end": "2026-07-22T15:00:00+00:00",
    }


def test_save_converts_values_for_postgresql() -> None:
    session = FakeSession()
    repository = NormalizedEventRepository(FakeSessionFactory(session))
    event = normalized_event()

    assert repository.save(event) is True

    assert session.params["quantity"] == Decimal("3.5")
    assert session.params["cost"] == Decimal("0.42")
    assert session.params["usage_start"] == datetime(
        2026,
        7,
        22,
        14,
        tzinfo=UTC,
    )
    assert session.params["normalized_payload"] == event


def test_duplicate_event_returns_false() -> None:
    repository = NormalizedEventRepository(
        FakeSessionFactory(FakeSession(rowcount=0))
    )

    assert repository.save(normalized_event()) is False


@pytest.mark.parametrize(
    "field",
    ["event_id", "case_id", "source", "category", "usage_type"],
)
def test_required_strings_must_be_present(field: str) -> None:
    event = normalized_event()
    event[field] = " "
    repository = NormalizedEventRepository(
        FakeSessionFactory(FakeSession())
    )

    with pytest.raises(NormalizedEventStorageError, match=field):
        repository.save(event)


def test_optional_string_rejects_non_string() -> None:
    event = normalized_event()
    event["region"] = 123
    repository = NormalizedEventRepository(
        FakeSessionFactory(FakeSession())
    )

    with pytest.raises(NormalizedEventStorageError, match="region"):
        repository.save(event)


@pytest.mark.parametrize("value", [True, "3.5", float("inf"), float("nan")])
def test_numeric_fields_require_finite_numbers(value: object) -> None:
    event = normalized_event()
    event["quantity"] = value
    repository = NormalizedEventRepository(
        FakeSessionFactory(FakeSession())
    )

    with pytest.raises(NormalizedEventStorageError, match="quantity"):
        repository.save(event)


@pytest.mark.parametrize(
    "value",
    ["not-a-date", "2026-07-22T14:00:00", 123],
)
def test_datetime_fields_require_valid_timezone(
    value: object,
) -> None:
    event = normalized_event()
    event["usage_start"] = value
    repository = NormalizedEventRepository(
        FakeSessionFactory(FakeSession())
    )

    with pytest.raises(NormalizedEventStorageError, match="usage_start"):
        repository.save(event)


def test_database_error_is_wrapped() -> None:
    repository = NormalizedEventRepository(
        FakeSessionFactory(
            FakeSession(error=SQLAlchemyError("database unavailable"))
        )
    )

    with pytest.raises(
        NormalizedEventStorageError,
        match="could not store",
    ):
        repository.save(normalized_event())
