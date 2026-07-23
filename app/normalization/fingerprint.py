import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import TypeAlias

JsonStructure: TypeAlias = dict[str, object]


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return "array"

    raise TypeError(f"unsupported JSON value type: {type(value).__name__}")


def get_schema_paths(payload: Mapping[str, object]) -> tuple[str, ...]:
    paths: list[str] = []

    def add_paths(value: object, parent: str = "") -> None:
        if isinstance(value, Mapping):
            for key in sorted(value):
                if not isinstance(key, str):
                    raise TypeError("payload field names must be strings")

                child = value[key]
                path = f"{parent}.{key}" if parent else key
                paths.append(f"{path}:{_json_type(child)}")
                add_paths(child, path)

        elif isinstance(value, Sequence) and not isinstance(
            value,
            (str, bytes, bytearray),
        ):
            # Arrays affect the fingerprint, but their individual items do not.
            return

    add_paths(payload)
    return tuple(paths)


def calculate_schema_fingerprint(payload: Mapping[str, object]) -> str:
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be an object")

    schema_paths = get_schema_paths(payload)
    encoded = json.dumps(
        schema_paths,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    digest = hashlib.sha256(encoded).hexdigest()
    return f"schema_{digest}"


def calculate_case_id(source: str, schema_fingerprint: str) -> str:
    if not isinstance(source, str):
        raise TypeError("source must be a string")
    if not isinstance(schema_fingerprint, str):
        raise TypeError("schema fingerprint must be a string")

    normalized_source = source.strip().lower()
    normalized_fingerprint = schema_fingerprint.strip()

    if not normalized_source:
        raise ValueError("source must not be blank")
    if not normalized_fingerprint:
        raise ValueError("schema fingerprint must not be blank")

    encoded = f"{normalized_source}:{normalized_fingerprint}".encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()

    return f"case_{digest}"
