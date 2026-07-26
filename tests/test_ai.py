import copy
import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from openai import OpenAIError

from app.ai.client import AIClient, AIClientError, AIResponseError
from app.ai.security import (
    AISecurityError,
    validate_ai_review,
    validate_keyword_blacklist,
    validate_mapping_structure,
)
from app.config import AI_CONFIG
from app.schemas.mappings import AIProposal

NORMALIZED_SCHEMA = {
    "properties": {
        "event_id": {"type": "string"},
        "case_id": {"type": "string"},
        "source": {"type": "string"},
        "category": {"type": "string"},
        "resource": {"type": ["string", "null"]},
        "quantity": {"type": ["number", "null"]},
    }
}


def proposal_json(source_path: str = "payload.resource_id") -> str:
    return json.dumps(
        {
            "fields": {
                "resource": [
                    {"operation": "copy", "from": source_path}
                ]
            }
        }
    )


def review_json(
    prompt_injection_detected: bool = False,
    data_leak_detected: bool = False,
) -> str:
    return json.dumps(
        {
            "prompt_injection_detected": prompt_injection_detected,
            "data_leak_detected": data_leak_detected,
        }
    )


class FakeCompletions:
    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = iter(responses)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(copy.deepcopy(kwargs))
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=response)
                )
            ]
        )


class FakeAIClient:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.chat = SimpleNamespace(
            completions=FakeCompletions(responses)
        )


@pytest.fixture
def ai_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(AI_CONFIG, "model", "generation-model")
    monkeypatch.setitem(AI_CONFIG, "review_model", "security-model")
    monkeypatch.setitem(AI_CONFIG, "max_attempts", 2)
    monkeypatch.setitem(AI_CONFIG, "max_completion_tokens", 1000)
    monkeypatch.setitem(AI_CONFIG, "reasoning_effort", "low")
    monkeypatch.setitem(AI_CONFIG, "log_mapping_responses", False)


def test_valid_mapping_structure() -> None:
    proposal = validate_mapping_structure(
        proposal_json(),
        {"resource_id": "i-123"},
        NORMALIZED_SCHEMA,
        {"category": "cloud"},
    )

    assert proposal.fields["resource"][0].source_path == "payload.resource_id"


def test_mapping_structure_accepts_json_fence() -> None:
    proposal = validate_mapping_structure(
        f"```json\n{proposal_json()}\n```",
        {"resource_id": "i-123"},
        NORMALIZED_SCHEMA,
        {"category": "cloud"},
    )

    assert proposal.fields["resource"][0].source_path == "payload.resource_id"


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        '{"fields":{"resource":[{"operation":"shell","command":"id"}]}}',
        '{"fields":{"resource":[{"operation":"cast","to":"string"}]}}',
    ],
)
def test_invalid_mapping_structure_is_rejected(response: str) -> None:
    with pytest.raises(AISecurityError, match="valid mapping proposal"):
        validate_mapping_structure(
            response,
            {"resource_id": "i-123"},
            NORMALIZED_SCHEMA,
            {"category": "cloud"},
        )


@pytest.mark.parametrize("target", ["event_id", "category", "unknown"])
def test_invalid_mapping_target_is_rejected(target: str) -> None:
    response = json.dumps(
        {
            "fields": {
                target: [
                    {
                        "operation": "copy",
                        "from": "payload.resource_id",
                    }
                ]
            }
        }
    )

    with pytest.raises(AISecurityError, match="invalid targets"):
        validate_mapping_structure(
            response,
            {"resource_id": "i-123"},
            NORMALIZED_SCHEMA,
            {"category": "cloud"},
        )


def test_missing_copy_path_is_rejected() -> None:
    with pytest.raises(AISecurityError, match="missing copy path"):
        validate_mapping_structure(
            proposal_json("payload.missing"),
            {"resource_id": "i-123"},
            NORMALIZED_SCHEMA,
            {"category": "cloud"},
        )


def test_keyword_blacklist_checks_payload_and_proposal() -> None:
    with pytest.raises(AISecurityError, match="blocked keyword"):
        validate_keyword_blacklist(
            {"message": "Ignore Previous Instructions"},
            ["ignore previous instructions"],
        )

    proposal = AIProposal.model_validate_json(
        proposal_json("payload.os.system")
    )
    with pytest.raises(AISecurityError, match="blocked keyword"):
        validate_keyword_blacklist(proposal, ["os.system"])


def test_safe_ai_review_is_accepted() -> None:
    validate_ai_review(review_json())


def test_ai_review_accepts_json_fence() -> None:
    validate_ai_review(f"```json\n{review_json()}\n```")


@pytest.mark.parametrize(
    ("injection", "leak"),
    [(True, False), (False, True), (True, True)],
)
def test_unsafe_ai_review_is_rejected(
    injection: bool,
    leak: bool,
) -> None:
    with pytest.raises(AISecurityError, match="security review"):
        validate_ai_review(review_json(injection, leak))


def test_invalid_ai_review_is_rejected() -> None:
    with pytest.raises(AISecurityError, match="review is invalid"):
        validate_ai_review('{"approved": true}')


def test_client_generates_then_security_reviews(
    ai_settings: None,
) -> None:
    fake = FakeAIClient([proposal_json(), review_json()])
    client = AIClient(client=fake)
    payload = {"resource_id": "i-123"}

    proposal = client.generate_mapping("aws", payload)

    assert "resource" in proposal.fields
    calls = fake.chat.completions.calls
    assert [call["model"] for call in calls] == [
        "generation-model",
        "security-model",
    ]
    generation_request = json.loads(calls[0]["messages"][1]["content"])
    assert "payload" not in generation_request
    assert "normalized_schema" not in generation_request
    assert generation_request["available_paths"] == {
        "payload.resource_id": "string"
    }
    assert generation_request["allowed_targets"]["resource"][
        "description"
    ] == "Infrastructure resource or AI model identifier."
    assert "category" not in generation_request["allowed_targets"]
    assert generation_request["excluded_targets"] == [
        "case_id",
        "category",
        "event_id",
        "source",
    ]
    assert "trusted_static_fields" not in generation_request
    assert "protected_fields" not in generation_request
    assert calls[0]["max_completion_tokens"] == 1000
    assert calls[0]["extra_body"] == {
        "reasoning": {
            "effort": "low",
            "exclude": True,
        }
    }
    review_request = json.loads(calls[1]["messages"][1]["content"])
    assert set(review_request) == {
        "untrusted_payload",
        "generated_output",
        "expected_output_schema",
    }
    assert review_request["untrusted_payload"] == payload
    assert "normalized_schema" not in review_request
    assert "allowed_operations" not in review_request


def test_client_retries_invalid_mapping(
    ai_settings: None,
) -> None:
    fake = FakeAIClient(
        ["invalid", proposal_json(), review_json()]
    )

    proposal = AIClient(client=fake).generate_mapping(
        "aws",
        {"resource_id": "i-123"},
    )

    assert "resource" in proposal.fields
    assert len(fake.chat.completions.calls) == 3
    second_generation = fake.chat.completions.calls[1]
    assert second_generation["model"] == "generation-model"
    assert len(second_generation["messages"]) == 4
    assert second_generation["messages"][2] == {
        "role": "assistant",
        "content": "invalid",
    }
    assert "not a valid mapping proposal" in (
        second_generation["messages"][3]["content"]
    )


def test_client_can_log_mapping_responses(
    ai_settings: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setitem(AI_CONFIG, "log_mapping_responses", True)
    fake = FakeAIClient([proposal_json(), review_json()])

    with caplog.at_level(logging.WARNING, logger="app.ai.client"):
        AIClient(client=fake).generate_mapping(
            "aws",
            {"resource_id": "i-123"},
        )

    assert "AI mapping response (attempt 1/2)" in caplog.text
    assert '"resource"' in caplog.text


def test_client_retries_security_rejection(
    ai_settings: None,
) -> None:
    fake = FakeAIClient(
        [
            proposal_json(),
            review_json(prompt_injection_detected=True),
            proposal_json(),
            review_json(),
        ]
    )

    proposal = AIClient(client=fake).generate_mapping(
        "aws",
        {"resource_id": "i-123"},
    )

    assert "resource" in proposal.fields
    assert len(fake.chat.completions.calls) == 4


def test_client_raises_after_max_attempts(
    ai_settings: None,
) -> None:
    fake = FakeAIClient(["invalid", "invalid"])

    with pytest.raises(
        AIResponseError,
        match="not a valid mapping proposal",
    ):
        AIClient(client=fake).generate_mapping(
            "aws",
            {"resource_id": "i-123"},
        )


def test_provider_error_remains_temporary(
    ai_settings: None,
) -> None:
    fake = FakeAIClient([OpenAIError("unavailable")])

    with pytest.raises(AIClientError, match="mapping request failed"):
        AIClient(client=fake).generate_mapping(
            "aws",
            {"resource_id": "i-123"},
        )


def test_blacklisted_payload_is_not_sent_to_ai(
    ai_settings: None,
) -> None:
    fake = FakeAIClient([])

    with pytest.raises(AISecurityError, match="blocked keyword"):
        AIClient(client=fake).generate_mapping(
            "aws",
            {"message": "ignore previous instructions"},
        )

    assert fake.chat.completions.calls == []
