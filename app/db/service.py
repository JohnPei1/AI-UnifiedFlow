"""Store normalized usage events."""

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from pydantic import JsonValue
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import SQLAlchemyError

from app.db.database import SessionFactory
from app.db.models import NormalizedEventRecord


class NormalizedEventStorageError(RuntimeError):
    pass


class NormalizedEventRepository:
    """Persist validated normalized events with idempotent event IDs."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def save(self, normalized_event: Mapping[str, JsonValue]) -> bool:
        """Store an event, returning false when it was already processed."""

        payload = dict(normalized_event)
        values = {
            "event_id": self._required_string(payload, "event_id"),
            "case_id": self._required_string(payload, "case_id"),
            "source": self._required_string(payload, "source"),
            "category": self._required_string(payload, "category"),
            "service": self._optional_string(payload, "service"),
            "resource": self._optional_string(payload, "resource"),
            "account_id": self._optional_string(payload, "account_id"),
            "project_id": self._optional_string(payload, "project_id"),
            "region": self._optional_string(payload, "region"),
            "usage_type": self._required_string(payload, "usage_type"),
            "input_units": self._optional_decimal(payload, "input_units"),
            "output_units": self._optional_decimal(payload, "output_units"),
            "quantity": self._optional_decimal(payload, "quantity"),
            "unit": self._optional_string(payload, "unit"),
            "cost": self._optional_decimal(payload, "cost"),
            "currency": self._optional_string(payload, "currency"),
            "usage_start": self._optional_datetime(payload, "usage_start"),
            "usage_end": self._optional_datetime(payload, "usage_end"),
            "normalized_payload": payload,
        }
        statement = (
            insert(NormalizedEventRecord)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["event_id"])
        )

        try:
            with self._session_factory.begin() as session:
                result = session.execute(statement)
            return result.rowcount == 1
        except SQLAlchemyError as error:
            raise NormalizedEventStorageError(
                f"could not store normalized event: {error}"
            ) from error

    @staticmethod
    def _required_string(
        payload: Mapping[str, JsonValue],
        field: str,
    ) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise NormalizedEventStorageError(
                f"normalized field '{field}' must be a non-blank string"
            )
        return value

    @staticmethod
    def _optional_string(
        payload: Mapping[str, JsonValue],
        field: str,
    ) -> str | None:
        value = payload.get(field)
        if value is None:
            return None
        if not isinstance(value, str):
            raise NormalizedEventStorageError(
                f"normalized field '{field}' must be a string or null"
            )
        return value

    @staticmethod
    def _optional_decimal(
        payload: Mapping[str, JsonValue],
        field: str,
    ) -> Decimal | None:
        value = payload.get(field)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise NormalizedEventStorageError(
                f"normalized field '{field}' must be a number or null"
            )

        try:
            decimal_value = Decimal(str(value))
        except InvalidOperation as error:
            raise NormalizedEventStorageError(
                f"normalized field '{field}' must be a finite number"
            ) from error
        if not decimal_value.is_finite():
            raise NormalizedEventStorageError(
                f"normalized field '{field}' must be a finite number"
            )
        return decimal_value

    @staticmethod
    def _optional_datetime(
        payload: Mapping[str, JsonValue],
        field: str,
    ) -> datetime | None:
        value = payload.get(field)
        if value is None:
            return None
        if not isinstance(value, str):
            raise NormalizedEventStorageError(
                f"normalized field '{field}' must be a date-time string or null"
            )

        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise NormalizedEventStorageError(
                f"normalized field '{field}' must be a valid date-time"
            ) from error

        if parsed.tzinfo is None:
            raise NormalizedEventStorageError(
                f"normalized field '{field}' must include a time zone"
            )

        # SQLite stores naive timestamps, so indexed values use UTC consistently.
        return parsed.astimezone(UTC).replace(tzinfo=None)
