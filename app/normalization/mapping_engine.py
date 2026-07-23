"""Apply declarative mappings to internal usage events."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TypeAlias

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from pydantic import JsonValue

from app.schemas.mappings import RuntimeMapping
from app.schemas.operations import (
    CastOperation,
    CopyOperation,
    MappingOperation,
    MultiplyOperation,
)
from app.schemas.requests import InternalUsageEvent

NormalizedEvent: TypeAlias = dict[str, JsonValue]

DEFAULT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "normalized_event_schema.json"
)
PROTECTED_FIELDS = frozenset({"event_id", "case_id", "source"})


class MappingEngineError(ValueError):
    """Base error raised when a mapping cannot produce a valid event."""

    pass


class MissingSourceFieldError(MappingEngineError):
    """Raised when a copy operation refers to a missing source field."""

    pass


class MappingOperationError(MappingEngineError):
    """Raised when a mapping operation cannot be applied."""

    pass


class NormalizedEventValidationError(MappingEngineError):
    """Raised when mapped output fails normalized event validation."""

    pass


class MappingEngine:
    """Transform internal usage events into validated normalized events."""

    def __init__(
        self,
        schema: Mapping[str, object] | None = None,
        schema_path: str | Path = DEFAULT_SCHEMA_PATH,
    ) -> None:
        if schema is None:
            schema = self._load_schema(Path(schema_path))

        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            raise ValueError(f"invalid normalized event schema: {error.message}") from error

        self._validator = Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        )

    def apply_mapping(
        self,
        event: InternalUsageEvent,
        mapping: RuntimeMapping,
    ) -> NormalizedEvent:
        """Apply a runtime mapping and validate the normalized result."""

        self._check_mapping_targets(mapping)

        normalized: NormalizedEvent = dict(mapping.static_fields)
        for target, operations in mapping.fields.items():
            normalized[target] = self._run_operations(
                target=target,
                operations=operations,
                event=event,
            )

        normalized.update(
            {
                "event_id": event.event_id,
                "case_id": event.case_id,
                "source": event.source,
            }
        )
        self.validate(normalized)
        return normalized

    def validate(self, normalized: Mapping[str, JsonValue]) -> None:
        """Validate a normalized event against the configured JSON Schema."""

        errors = sorted(
            self._validator.iter_errors(normalized),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if not errors:
            return

        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path)
        if location:
            raise NormalizedEventValidationError(
                f"normalized field '{location}' is invalid: {error.message}"
            )
        raise NormalizedEventValidationError(
            f"normalized event is invalid: {error.message}"
        )

    @staticmethod
    def _load_schema(path: Path) -> Mapping[str, object]:
        try:
            with path.open(encoding="utf-8") as schema_file:
                schema = json.load(schema_file)
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"could not load normalized event schema: {error}") from error

        if not isinstance(schema, dict):
            raise ValueError("normalized event schema must be a JSON object")
        return schema

    @staticmethod
    def _check_mapping_targets(mapping: RuntimeMapping) -> None:
        targets = mapping.fields.keys() | mapping.static_fields.keys()
        protected_targets = targets & PROTECTED_FIELDS
        if protected_targets:
            names = ", ".join(sorted(protected_targets))
            raise MappingOperationError(
                f"mapping cannot set protected fields: {names}"
            )

    def _run_operations(
        self,
        target: str,
        operations: list[MappingOperation],
        event: InternalUsageEvent,
    ) -> JsonValue:
        value: JsonValue | None = None

        for index, operation in enumerate(operations):
            try:
                value = self._apply_operation(operation, value, event)
            except MappingEngineError as error:
                raise type(error)(
                    f"mapping for '{target}' failed at operation {index + 1}: {error}"
                ) from error

        return value

    def _apply_operation(
        self,
        operation: MappingOperation,
        current_value: JsonValue | None,
        event: InternalUsageEvent,
    ) -> JsonValue:
        if isinstance(operation, CopyOperation):
            return self._resolve_path(operation.source_path, event)
        if isinstance(operation, CastOperation):
            return self._cast(current_value, operation.to)
        if isinstance(operation, MultiplyOperation):
            return self._multiply(current_value, operation.by)

        raise MappingOperationError(
            f"unsupported operation: {type(operation).__name__}"
        )

    @staticmethod
    def _resolve_path(path: str, event: InternalUsageEvent) -> JsonValue:
        if path == "source":
            return event.source

        current: object = event.payload
        for segment in path.removeprefix("payload.").split("."):
            if not isinstance(current, Mapping) or segment not in current:
                raise MissingSourceFieldError(f"source field '{path}' was not found")
            current = current[segment]

        return current

    @staticmethod
    def _cast(value: JsonValue | None, target_type: str) -> JsonValue:
        if value is None or isinstance(value, (dict, list)):
            raise MappingOperationError(
                f"cannot cast {type(value).__name__} to {target_type}"
            )

        try:
            if target_type == "string":
                if isinstance(value, bool):
                    return "true" if value else "false"
                return str(value)

            if target_type == "integer":
                if isinstance(value, bool):
                    raise ValueError
                if isinstance(value, float) and not value.is_integer():
                    raise ValueError
                return int(value)

            if target_type == "float":
                if isinstance(value, bool):
                    raise ValueError
                return float(value)

            if target_type == "boolean":
                if isinstance(value, bool):
                    return value
                if isinstance(value, str):
                    normalized = value.strip().lower()
                    if normalized == "true":
                        return True
                    if normalized == "false":
                        return False
                if isinstance(value, (int, float)) and value in (0, 1):
                    return bool(value)
                raise ValueError
        except (TypeError, ValueError, OverflowError) as error:
            raise MappingOperationError(
                f"cannot cast {value!r} to {target_type}"
            ) from error

        raise MappingOperationError(f"unsupported cast type: {target_type}")

    @staticmethod
    def _multiply(
        value: JsonValue | None,
        multiplier: int | float,
    ) -> int | float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or isinstance(multiplier, bool)
        ):
            raise MappingOperationError(
                "multiply requires a numeric value and multiplier"
            )
        return value * multiplier
