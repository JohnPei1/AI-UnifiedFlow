"""Cast scalar JSON values used by mapping operations."""

import math
from collections.abc import Callable

from pydantic import JsonValue

from app.schemas.operations import CastType

ScalarValue = str | int | float | bool
CastFunction = Callable[[ScalarValue], ScalarValue]


class CastValueError(ValueError):
    """Raised when a value cannot be converted to the requested type."""


def _to_string(value: ScalarValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _to_integer(value: ScalarValue) -> int:
    if isinstance(value, bool):
        raise ValueError

    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError

    result = int(value)

    if isinstance(value, str) and value.strip() != str(result):
        raise ValueError

    return result


def _to_float(value: ScalarValue) -> float:
    if isinstance(value, bool):
        raise ValueError

    result = float(value)

    if not math.isfinite(result):
        raise ValueError

    return result


def _to_boolean(value: ScalarValue) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()

        if normalized == "true":
            return True
        if normalized == "false":
            return False

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 0:
            return False
        if value == 1:
            return True

    raise ValueError


_CASTERS: dict[CastType, CastFunction] = {
    "string": _to_string,
    "integer": _to_integer,
    "float": _to_float,
    "boolean": _to_boolean,
}


def cast_value(value: JsonValue, target_type: CastType) -> ScalarValue:
    """Convert a scalar JSON value to a supported mapping type."""

    if not isinstance(value, (str, int, float, bool)):
        raise CastValueError(
            f"cannot cast {type(value).__name__} to {target_type}"
        )

    caster = _CASTERS.get(target_type)
    if caster is None:
        raise CastValueError(f"unsupported cast type: {target_type}")

    try:
        return caster(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise CastValueError(
            f"cannot cast {value!r} to {target_type}"
        ) from error
