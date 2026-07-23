from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

NonBlankString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
CastType = Literal["string", "integer", "float", "boolean"]


class StrictOperationModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
        populate_by_name=True,
    )


class CopyOperation(StrictOperationModel):
    operation: Literal["copy"]
    source_path: NonBlankString = Field(alias="from")

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        if value == "source":
            return value

        if not value.startswith("payload."):
            raise ValueError("copy path must be 'source' or begin with 'payload.'")

        segments = value.split(".")
        if any(not segment for segment in segments):
            raise ValueError("copy path cannot contain empty segments")

        return value


class CastOperation(StrictOperationModel):
    operation: Literal["cast"]
    to: CastType


class MultiplyOperation(StrictOperationModel):
    operation: Literal["multiply"]
    by: int | float

    @field_validator("by")
    @classmethod
    def reject_boolean_multiplier(cls, value: int | float) -> int | float:
        # Python treats booleans as integers, but they are not valid multipliers here.
        if isinstance(value, bool):
            raise ValueError("multiply factor must be numeric, not boolean")
        return value


MappingOperation = Annotated[
    CopyOperation | CastOperation | MultiplyOperation,
    Field(discriminator="operation"),
]
