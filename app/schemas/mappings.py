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
SchemaSignatureEntry = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[^:]+:(null|boolean|integer|number|string|object|array)$",
    ),
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
SchemaSignature = Annotated[list[SchemaSignatureEntry], Field(min_length=1)]


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


class PredefinedMapping(StrictModel):
    name: NonBlankString
    version: Annotated[int, Field(ge=1)] = 1
    schema_signature: SchemaSignature
    static_fields: StaticFields = Field(default_factory=dict)
    fields: MappingFields

    _validate_fields = field_validator("fields")(_validate_operation_pipelines)

    @model_validator(mode="after")
    def validate_signature_and_targets(self) -> Self:
        signature_types: dict[str, str] = {}
        for entry in self.schema_signature:
            path, json_type = entry.rsplit(":", 1)
            if any(not segment for segment in path.split(".")):
                raise ValueError(f"invalid schema signature path: {path}")
            if path in signature_types:
                raise ValueError(f"duplicate schema signature path: {path}")
            signature_types[path] = json_type

        for path in signature_types:
            segments = path.split(".")
            for end in range(1, len(segments)):
                parent = ".".join(segments[:end])
                if signature_types.get(parent) != "object":
                    raise ValueError(
                        f"schema signature parent '{parent}' must be an object"
                    )

        for operations in self.fields.values():
            copy_path = operations[0].source_path
            if copy_path == "source":
                continue

            payload_path = copy_path.removeprefix("payload.")
            if payload_path not in signature_types:
                raise ValueError(
                    f"copy path '{copy_path}' is missing from schema_signature"
                )

        duplicate_targets = self.static_fields.keys() & self.fields.keys()
        if duplicate_targets:
            targets = ", ".join(sorted(duplicate_targets))
            raise ValueError(
                f"fields and static_fields contain the same target: {targets}"
            )
        return self


class PredefinedMappingFile(StrictModel):
    source: NonBlankString
    mappings: Annotated[list[PredefinedMapping], Field(min_length=1)]

    @field_validator("source", mode="before")
    @classmethod
    def normalize_source(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


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
