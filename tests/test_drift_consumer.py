from collections.abc import Callable
from unittest.mock import Mock

import pytest

from app.ai.client import AIClientError, AIResponseError
from app.ai.security import AISecurityError
from app.messaging import drift_consumer
from app.messaging.kafka_client import KafkaPublishError
from app.normalization.mapping_engine import MappingEngineError
from app.schemas.mappings import AIProposal, RuntimeMapping
from app.schemas.requests import FailedEvent, InternalUsageEvent


@pytest.fixture
def proposal() -> AIProposal:
    return AIProposal(
        fields={
            "resource": [
                {"operation": "copy", "from": "payload.resource_id"}
            ]
        }
    )


def configure_message(
    monkeypatch: pytest.MonkeyPatch,
    event: InternalUsageEvent,
) -> None:
    monkeypatch.setattr(
        drift_consumer,
        "decode_message",
        lambda message, model_type: event,
    )


def test_existing_mapping_avoids_ai(
    usage_event: InternalUsageEvent,
    mapping_factory: Callable[..., RuntimeMapping],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapping_store = Mock()
    mapping_store.get.return_value = mapping_factory()
    ai_client = Mock()
    publish = Mock()
    commit = Mock()
    configure_message(monkeypatch, usage_event)
    monkeypatch.setattr(drift_consumer, "publish_event", publish)
    monkeypatch.setattr(drift_consumer, "commit_message", commit)

    drift_consumer.process_drift_message(
        Mock(),
        Mock(),
        mapping_store,
        Mock(),
        ai_client,
        Mock(),
    )

    ai_client.generate_mapping.assert_not_called()
    publish.assert_called_once()
    assert publish.call_args.kwargs["topic"] == drift_consumer.RAW_EVENTS_TOPIC
    commit.assert_called_once()


def test_missing_mapping_calls_ai_saves_and_republishes(
    usage_event: InternalUsageEvent,
    proposal: AIProposal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    mapping_store = Mock()
    mapping_store.get.return_value = None
    mapping_store.save.side_effect = lambda mapping: calls.append("save")
    mapping_engine = Mock()
    ai_client = Mock()
    ai_client.generate_mapping.return_value = proposal
    publish = Mock(
        side_effect=lambda *args, **kwargs: calls.append("publish")
    )
    commit = Mock(side_effect=lambda consumer, message: calls.append("commit"))
    configure_message(monkeypatch, usage_event)
    monkeypatch.setattr(drift_consumer, "publish_event", publish)
    monkeypatch.setattr(drift_consumer, "commit_message", commit)

    drift_consumer.process_drift_message(
        Mock(),
        Mock(),
        mapping_store,
        mapping_engine,
        ai_client,
        Mock(),
    )

    ai_client.generate_mapping.assert_called_once_with(
        usage_event.source,
        usage_event.payload,
    )
    mapping_engine.apply_mapping.assert_called_once()
    mapping_store.save.assert_called_once()
    saved = mapping_store.save.call_args.args[0]
    assert saved.case_id == usage_event.case_id
    assert saved.created_by == "ai"
    assert calls == ["save", "publish", "commit"]


@pytest.mark.parametrize(
    "error",
    [
        AISecurityError("invalid AI output"),
        AIResponseError("unsupported operation"),
    ],
)
def test_invalid_ai_output_goes_to_failed_events(
    usage_event: InternalUsageEvent,
    error: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapping_store = Mock()
    mapping_store.get.return_value = None
    ai_client = Mock()
    ai_client.generate_mapping.side_effect = error
    publish = Mock()
    commit = Mock()
    configure_message(monkeypatch, usage_event)
    monkeypatch.setattr(drift_consumer, "publish_event", publish)
    monkeypatch.setattr(drift_consumer, "commit_message", commit)

    drift_consumer.process_drift_message(
        Mock(),
        Mock(),
        mapping_store,
        Mock(),
        ai_client,
        Mock(),
    )

    mapping_store.save.assert_not_called()
    assert publish.call_args.kwargs["topic"] == drift_consumer.FAILED_EVENTS_TOPIC
    failed_event = publish.call_args.kwargs["event"]
    assert isinstance(failed_event, FailedEvent)
    assert failed_event.reason == str(error)
    commit.assert_called_once()


def test_mapping_failure_on_current_event_is_not_saved(
    usage_event: InternalUsageEvent,
    proposal: AIProposal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapping_store = Mock()
    mapping_store.get.return_value = None
    mapping_engine = Mock()
    mapping_engine.apply_mapping.side_effect = MappingEngineError(
        "mapping failed"
    )
    ai_client = Mock()
    ai_client.generate_mapping.return_value = proposal
    publish = Mock()
    configure_message(monkeypatch, usage_event)
    monkeypatch.setattr(drift_consumer, "publish_event", publish)
    monkeypatch.setattr(drift_consumer, "commit_message", Mock())

    drift_consumer.process_drift_message(
        Mock(),
        Mock(),
        mapping_store,
        mapping_engine,
        ai_client,
        Mock(),
    )

    mapping_store.save.assert_not_called()
    assert publish.call_args.kwargs["topic"] == drift_consumer.FAILED_EVENTS_TOPIC


def test_redelivery_uses_saved_mapping_without_another_ai_call(
    usage_event: InternalUsageEvent,
    proposal: AIProposal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: RuntimeMapping | None = None
    mapping_store = Mock()

    def get_mapping(case_id: str) -> RuntimeMapping | None:
        return saved

    def save_mapping(mapping: RuntimeMapping) -> RuntimeMapping:
        nonlocal saved
        saved = mapping
        return mapping

    mapping_store.get.side_effect = get_mapping
    mapping_store.save.side_effect = save_mapping
    ai_client = Mock()
    ai_client.generate_mapping.return_value = proposal
    configure_message(monkeypatch, usage_event)
    publish = Mock()
    commit = Mock()
    monkeypatch.setattr(drift_consumer, "publish_event", publish)
    monkeypatch.setattr(drift_consumer, "commit_message", commit)

    for _ in range(2):
        drift_consumer.process_drift_message(
            Mock(),
            Mock(),
            mapping_store,
            Mock(),
            ai_client,
            Mock(),
        )

    ai_client.generate_mapping.assert_called_once()
    mapping_store.save.assert_called_once()
    assert publish.call_count == 2
    assert commit.call_count == 2


def test_temporary_ai_error_does_not_commit(
    usage_event: InternalUsageEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapping_store = Mock()
    mapping_store.get.return_value = None
    ai_client = Mock()
    ai_client.generate_mapping.side_effect = AIClientError("unavailable")
    commit = Mock()
    configure_message(monkeypatch, usage_event)
    monkeypatch.setattr(drift_consumer, "commit_message", commit)

    with pytest.raises(AIClientError):
        drift_consumer.process_drift_message(
            Mock(),
            Mock(),
            mapping_store,
            Mock(),
            ai_client,
            Mock(),
        )

    mapping_store.save.assert_not_called()
    commit.assert_not_called()


def test_publish_failure_after_save_is_retried_without_ai(
    usage_event: InternalUsageEvent,
    proposal: AIProposal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: RuntimeMapping | None = None
    mapping_store = Mock()
    mapping_store.get.side_effect = lambda case_id: saved

    def save_mapping(mapping: RuntimeMapping) -> RuntimeMapping:
        nonlocal saved
        saved = mapping
        return mapping

    mapping_store.save.side_effect = save_mapping
    ai_client = Mock()
    ai_client.generate_mapping.return_value = proposal
    publish = Mock(
        side_effect=[KafkaPublishError("unavailable"), None]
    )
    commit = Mock()
    configure_message(monkeypatch, usage_event)
    monkeypatch.setattr(drift_consumer, "publish_event", publish)
    monkeypatch.setattr(drift_consumer, "commit_message", commit)

    with pytest.raises(KafkaPublishError):
        drift_consumer.process_drift_message(
            Mock(),
            Mock(),
            mapping_store,
            Mock(),
            ai_client,
            Mock(),
        )

    drift_consumer.process_drift_message(
        Mock(),
        Mock(),
        mapping_store,
        Mock(),
        ai_client,
        Mock(),
    )

    ai_client.generate_mapping.assert_called_once()
    mapping_store.save.assert_called_once()
    commit.assert_called_once()
