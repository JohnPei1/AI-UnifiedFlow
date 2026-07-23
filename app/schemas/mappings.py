from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.schemas.operations import CopyOperation, MappingOperation

NonBlankString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
TargetFieldName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
CreatedBy = Literal["user", "ai"]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
        populate_by_name=True,
    )


OperationPipeline = Annotated[list[MappingOperation], Field(min_length=1)]
MappingFields = Annotated[dict[TargetFieldName, OperationPipeline], Field(min_length=1)]
StaticFields = dict[TargetFieldName, JsonValue]


def _validate_operation_pipelines(fields: MappingFields) -> MappingFields:
    for target, operations in fields.items():
        if not isinstance(operations[0], CopyOperation):
            raise ValueError(f"mapping for '{target}' must begin with a copy operation")
        if any(isinstance(operation, CopyOperation) for operation in operations[1:]):
            raise ValueError(f"mapping for '{target}' may only copy in its first operation")
    return fields


class AIProposal(StrictModel):
    fields: MappingFields

    _validate_fields = field_validator("fields")(_validate_operation_pipelines)


class RuntimeMapping(StrictModel):
    case_id: NonBlankString
    source: NonBlankString
    schema_fingerprint: NonBlankString
    version: Annotated[int, Field(ge=1)] = 1
    created_by: CreatedBy
    static_fields: StaticFields = Field(default_factory=dict)
    fields: MappingFields

    _validate_fields = field_validator("fields")(_validate_operation_pipelines)

    @field_validator("source", mode="before")
    @classmethod
    def normalize_source(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @model_validator(mode="after")
    def reject_duplicate_targets(self) -> Self:
        duplicate_targets = self.static_fields.keys() & self.fields.keys()
        if duplicate_targets:
            targets = ", ".join(sorted(duplicate_targets))
            raise ValueError(
                f"fields and static_fields contain the same target: {targets}"
            )
        return self
