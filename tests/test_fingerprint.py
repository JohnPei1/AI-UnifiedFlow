import pytest

from app.normalization.fingerprint import (
    calculate_case_id,
    calculate_schema_fingerprint,
    get_schema_paths,
)


def test_fingerprint_is_stable_regardless_of_key_order() -> None:
    first = {"usage": {"count": 1, "unit": "hours"}, "active": True}
    second = {"active": False, "usage": {"unit": "seconds", "count": 99}}

    assert calculate_schema_fingerprint(first) == calculate_schema_fingerprint(
        second
    )


def test_schema_paths_include_nested_types() -> None:
    payload = {
        "active": True,
        "metadata": {"attempt": 1, "ratio": 1.5, "note": None},
        "items": [{"value": 1}],
    }

    assert get_schema_paths(payload) == (
        "active:boolean",
        "items:array",
        "metadata:object",
        "metadata.attempt:integer",
        "metadata.note:null",
        "metadata.ratio:number",
    )


def test_renamed_field_changes_fingerprint() -> None:
    assert calculate_schema_fingerprint(
        {"usage_amount": 1}
    ) != calculate_schema_fingerprint({"quantity": 1})


def test_moved_field_changes_fingerprint() -> None:
    assert calculate_schema_fingerprint(
        {"usage": {"amount": 1}}
    ) != calculate_schema_fingerprint({"amount": 1})


def test_values_with_same_structure_share_fingerprint() -> None:
    assert calculate_schema_fingerprint(
        {"count": 1, "label": "first"}
    ) == calculate_schema_fingerprint({"count": 999, "label": "second"})


def test_different_source_changes_case_id() -> None:
    fingerprint = calculate_schema_fingerprint({"count": 1})

    assert calculate_case_id("aws", fingerprint) != calculate_case_id(
        "openai",
        fingerprint,
    )


def test_case_id_normalizes_source() -> None:
    fingerprint = calculate_schema_fingerprint({"count": 1})

    assert calculate_case_id(" AWS ", fingerprint) == calculate_case_id(
        "aws",
        fingerprint,
    )


def test_arrays_do_not_inspect_individual_items() -> None:
    assert calculate_schema_fingerprint(
        {"items": [{"name": "first"}]}
    ) == calculate_schema_fingerprint({"items": [1, 2, 3]})


def test_non_json_value_is_rejected() -> None:
    with pytest.raises(TypeError, match="unsupported JSON value type"):
        calculate_schema_fingerprint({"value": object()})
