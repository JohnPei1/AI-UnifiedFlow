"""Validate AI mapping proposals and security reviews."""

import json
from collections.abc import Mapping

from pydantic import JsonValue, ValidationError

from app.normalization.fingerprint import get_schema_paths
from app.schemas.mappings import AIProposal, AIReview
from app.schemas.operations import CopyOperation

PROTECTED_TARGETS = frozenset({"event_id", "case_id", "source"})


class AISecurityError(ValueError):
    pass


def validate_mapping_structure(
    response_text: str | None,
    payload: Mapping[str, JsonValue],
    normalized_schema: Mapping[str, object],
    static_fields: Mapping[str, JsonValue],
) -> AIProposal:
    """Parse and validate the structure of AI-generated mapping."""

    if response_text is None:
        raise AISecurityError("AI response has no content")

    try:
        document = json.loads(response_text)
        if not isinstance(document, dict):
            raise AISecurityError("AI response must be one JSON object")
        proposal = AIProposal.model_validate(document)
    except (json.JSONDecodeError, ValidationError) as error:
        raise AISecurityError("AI response is not a valid mapping proposal") from error

    valid_targets = set(normalized_schema["properties"])
    proposal_targets = set(proposal.fields)
    invalid_targets = proposal_targets - valid_targets
    invalid_targets |= proposal_targets & PROTECTED_TARGETS
    invalid_targets |= proposal_targets & static_fields.keys()
    if invalid_targets:
        names = ", ".join(sorted(invalid_targets))
        raise AISecurityError(f"AI response contains invalid targets: {names}")

    payload_paths = {
        f"payload.{entry.rsplit(':', 1)[0]}"
        for entry in get_schema_paths(payload)
    }
    payload_paths.add("source")

    for operations in proposal.fields.values():
        for operation in operations:
            if (
                isinstance(operation, CopyOperation)
                and operation.source_path not in payload_paths
            ):
                raise AISecurityError(
                    f"AI response uses missing copy path: {operation.source_path}"
                )

    return proposal


def validate_ai_review(response_text: str | None) -> None:
    """Reject output flagged for prompt injection or data leakage."""

    if response_text is None:
        raise AISecurityError("AI review has no content")

    try:
        document = json.loads(response_text)
        if not isinstance(document, dict):
            raise AISecurityError("AI review must be one JSON object")
        review = AIReview.model_validate(document)
    except (json.JSONDecodeError, ValidationError) as error:
        raise AISecurityError("AI review is invalid") from error

    if review.prompt_injection_detected or review.data_leak_detected:
        raise AISecurityError("AI security review rejected the output")


def validate_keyword_blacklist(
    proposal: AIProposal,
    keyword_blacklist: list[str],
) -> None:
    """Reject configured keywords in an AI-generated proposal."""

    content = proposal.model_dump_json(by_alias=True)
    _reject_blacklisted_content(content, keyword_blacklist)


def _reject_blacklisted_content(
    content: str,
    keyword_blacklist: list[str],
) -> None:
    normalized = content.casefold()
    for keyword in keyword_blacklist:
        if keyword.casefold() in normalized:
            raise AISecurityError("content contains a blocked keyword")
