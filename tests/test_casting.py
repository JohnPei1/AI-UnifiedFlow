import pytest

from app.normalization.casting import CastValueError, cast_value


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, "true"), (False, "false"), (12, "12"), (1.5, "1.5")],
)
def test_cast_to_string(value: object, expected: str) -> None:
    assert cast_value(value, "string") == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("12", 12), ("-3", -3), (12.0, 12)],
)
def test_cast_to_integer(value: object, expected: int) -> None:
    assert cast_value(value, "integer") == expected


@pytest.mark.parametrize("value", [True, "01", "1.0", 1.5, float("inf")])
def test_invalid_integer_cast(value: object) -> None:
    with pytest.raises(CastValueError, match="cannot cast"):
        cast_value(value, "integer")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1.5", 1.5), (2, 2.0), (-3.5, -3.5)],
)
def test_cast_to_float(value: object, expected: float) -> None:
    assert cast_value(value, "float") == expected


@pytest.mark.parametrize("value", [True, "nan", "inf", float("inf")])
def test_invalid_float_cast(value: object) -> None:
    with pytest.raises(CastValueError, match="cannot cast"):
        cast_value(value, "float")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        (" FALSE ", False),
        (1, True),
        (0.0, False),
        (True, True),
    ],
)
def test_cast_to_boolean(value: object, expected: bool) -> None:
    assert cast_value(value, "boolean") is expected


@pytest.mark.parametrize("value", ["yes", 2, -1, 0.5])
def test_invalid_boolean_cast(value: object) -> None:
    with pytest.raises(CastValueError, match="cannot cast"):
        cast_value(value, "boolean")


@pytest.mark.parametrize("value", [None, {}, []])
def test_non_scalar_cast_is_rejected(value: object) -> None:
    with pytest.raises(CastValueError, match="cannot cast"):
        cast_value(value, "string")
