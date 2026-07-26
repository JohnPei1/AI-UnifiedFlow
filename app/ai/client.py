"""Request mapping proposals through OpenRouter."""

import json
import logging
from collections.abc import Mapping
from pathlib import Path

from openai import OpenAI as OpenAIClient
from openai import OpenAIError
from pydantic import JsonValue

from app.ai.security import (
    AISecurityError,
    PROTECTED_TARGETS,
    validate_mapping_structure,
    validate_ai_review,
    validate_keyword_blacklist,
)
from app.config import (
    AI_CONFIG,
    AI_STATIC_FIELDS,
    AISecrets,
    NORMALIZED_EVENT_SCHEMA_PATH,
)
from app.normalization.fingerprint import get_schema_paths
from app.schemas.mappings import AIProposal, AIReview

logger = logging.getLogger(__name__)


class AIClientError(RuntimeError):
    pass


class AIResponseError(AIClientError):
    pass


class AIClient:
    """Generate validated mapping proposals through an OpenAI-compatible API."""

    def __init__(
        self,
        client: OpenAIClient | None = None,
        schema_path: str | Path = NORMALIZED_EVENT_SCHEMA_PATH,
    ) -> None:
        self._normalized_schema = json.loads(
            Path(schema_path).read_text(encoding="utf-8")
        )
        self._model = AI_CONFIG["model"]
        self._review_model = AI_CONFIG["review_model"]
        self._temperature = AI_CONFIG["temperature"]
        self._max_attempts = AI_CONFIG["max_attempts"]
        self._max_completion_tokens = AI_CONFIG["max_completion_tokens"]
        self._reasoning_effort = AI_CONFIG["reasoning_effort"]
        self._log_mapping_responses = AI_CONFIG["log_mapping_responses"]
        self._system_prompt = "\n".join(AI_CONFIG["system_prompt"])
        self._review_system_prompt = "\n".join(
            AI_CONFIG["review_system_prompt"]
        )
        self._allowed_operations = AI_CONFIG["allowed_operations"]
        self._keyword_blacklist = AI_CONFIG["keyword_blacklist"]
        self._client = client or OpenAIClient(
            api_key=AISecrets().openrouter_api_key.get_secret_value(),
            base_url=AI_CONFIG["base_url"],
            timeout=AI_CONFIG["timeout_seconds"],
            max_retries=0,
        )

    def generate_mapping(
        self,
        source: str,
        payload: Mapping[str, JsonValue],
    ) -> AIProposal:
        """Generate and validate a mapping proposal for one payload structure."""

        validate_keyword_blacklist(
            payload,
            self._keyword_blacklist
        )

        # Prepare the request data for the AI model
        static_fields = AI_STATIC_FIELDS[source]
        excluded_targets = PROTECTED_TARGETS | static_fields.keys()
        available_paths = {
            f"payload.{path}": json_type
            for entry in get_schema_paths(payload)
            for path, json_type in [entry.rsplit(":", 1)]
        }
        request_data = {
            "source": source,
            "available_paths": available_paths,
            "allowed_targets": {
                target: definition
                for target, definition in self._normalized_schema[
                    "properties"
                ].items()
                if target not in excluded_targets
            },
            "excluded_targets": sorted(excluded_targets),
            "allowed_operations": self._allowed_operations,
            "expected_output_schema": AIProposal.model_json_schema(
                by_alias=True
            ),
        }
        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": json.dumps(request_data, ensure_ascii=False),
            },
        ]
        last_error: AISecurityError | None = None

        # Attempt to generate a valid mapping proposal, retrying if validation fails
        for attempt in range(1, self._max_attempts + 1):
            # If the previous attempt failed, inform the AI model to correct its response
            if last_error is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"The previous response failed validation: "
                            f"{last_error}. "
                            "Return a corrected mapping."
                        ),
                    }
                )

            try:
                # Request a mapping proposal from the AI model
                content = self._request(
                    self._model,
                    messages,
                    "AI mapping request failed",
                )
                if self._log_mapping_responses:
                    logger.warning(
                        "AI mapping response (attempt %s/%s): %s",
                        attempt,
                        self._max_attempts,
                        content,
                    )
                if content is not None:
                    messages.append(
                        {"role": "assistant", "content": content}
                    )

                # Structural validation of the AI-generated mapping 
                proposal = validate_mapping_structure(
                    content,
                    payload,
                    self._normalized_schema,
                    static_fields,
                )

                # Keyword blacklist validation
                validate_keyword_blacklist(
                    proposal,
                    self._keyword_blacklist,
                )

                # AI peer review
                self._review_output(payload, content)
                return proposal
            except AISecurityError as error:
                last_error = error

        raise AIResponseError(
            "AI did not return a valid mapping proposal: "
            f"{last_error}"
        ) from last_error

    def _review_output(
        self,
        payload: Mapping[str, JsonValue],
        generated_output: str | None,
    ) -> None:
        """Request a peer review of the AI-generated mapping proposal."""
        request_data = {
            "untrusted_payload": payload,
            "generated_output": generated_output,
            "expected_output_schema": AIReview.model_json_schema(),
        }
        messages = [
            {"role": "system", "content": self._review_system_prompt},
            {
                "role": "user",
                "content": json.dumps(request_data, ensure_ascii=False),
            },
        ]
        content = self._request(
            self._review_model,
            messages,
            "AI review request failed",
        )
        validate_ai_review(content)

    def _request(
        self,
        model: str,
        messages: list[dict[str, str]],
        error_message: str,
    ) -> str | None:
        """Send a request to the AI model."""
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=self._temperature,
                max_completion_tokens=self._max_completion_tokens,
                response_format={"type": "json_object"},
                extra_body={
                    "reasoning": {
                        "effort": self._reasoning_effort,
                        "exclude": True,
                    }
                },
            )
        except OpenAIError as error:
            raise AIClientError(error_message) from error

        return (
            response.choices[0].message.content
            if response.choices
            else None
        )
