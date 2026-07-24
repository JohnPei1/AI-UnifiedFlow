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

from app.schemas.mappings import RuntimeMapping

NonBlankString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
Payload = dict[str, JsonValue]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
    )


class SourcePayloadModel(StrictModel):
    source: NonBlankString
    payload: Payload

    @field_validator("source", mode="before")
    @classmethod
    def normalize_source(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class UsageEventRequest(SourcePayloadModel):
    pass


class MappingResolveRequest(SourcePayloadModel):
    pass


class InternalUsageEvent(SourcePayloadModel):
    """Internal event envelope published to Kafka topics."""

    event_id: NonBlankString
    case_id: NonBlankString
    schema_fingerprint: NonBlankString


class FailedEvent(StrictModel):
    event: InternalUsageEvent
    reason: NonBlankString


class EventAcceptedResponse(StrictModel):
    event_id: NonBlankString
    case_id: NonBlankString
    status: Literal["accepted"] = "accepted"


class MappingResolveResponse(StrictModel):
    case_id: NonBlankString
    status: Literal["available", "not_found"]
    mapping: RuntimeMapping | None

    @model_validator(mode="after")
    def validate_status_matches_mapping(self) -> Self:
        if self.status == "available" and self.mapping is None:
            raise ValueError("an available mapping response must include a mapping")
        if self.status == "not_found" and self.mapping is not None:
            raise ValueError("a not_found mapping response cannot include a mapping")
        return self


class HealthResponse(StrictModel):
    kafka: Literal["healthy", "unhealthy"]
    postgresql: Literal["healthy", "unhealthy"]
