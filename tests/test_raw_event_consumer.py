from collections.abc import Callable
from unittest.mock import Mock

import pytest

from app.db.service import NormalizedEventStorageError
from app.messaging import raw_event_consumer
from app.messaging.kafka_client import KafkaPublishError
from app.normalization.mapping_engine import MappingEngineError
from app.schemas.mappings import RuntimeMapping
from app.schemas.requests import FailedEvent, InternalUsageEvent


def test_existing_mapping_stores_normalized_event_then_commits(
    usage_event: InternalUsageEvent,
    mapping_factory: Callable[..., RuntimeMapping],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    normalized = {
        "event_id": usage_event.event_id,
        "case_id": usage_event.case_id,
        "source": usage_event.source,
        "category": "cloud",
        "usage_type": "compute_time",
    }
    mapping_store = Mock()
    mapping_store.get.return_value = mapping_factory()
    mapping_engine = Mock()
    mapping_engine.apply_mapping.return_value = normalized
    repository = Mock()
    repository.save.side_effect = lambda event: calls.append("save") or True
    commit = Mock(side_effect=lambda consumer, message: calls.append("commit"))
    monkeypatch.setattr(
        raw_event_consumer,
        "decode_message",
        lambda message, model_type: usage_event,
    )
    monkeypatch.setattr(raw_event_consumer, "commit_message", commit)

    raw_event_consumer.process_raw_message(
        Mock(),
        Mock(),
        mapping_store,
        mapping_engine,
        repository,
        Mock(),
    )

    assert calls == ["save", "commit"]
    repository.save.assert_called_once_with(normalized)


def test_missing_mapping_publishes_schema_drift_then_commits(
    usage_event: InternalUsageEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    mapping_store = Mock()
    mapping_store.get.return_value = None
    publish = Mock(
        side_effect=lambda producer, topic, event, key: calls.append(
            "publish"
        )
    )
    commit = Mock(side_effect=lambda consumer, message: calls.append("commit"))
    monkeypatch.setattr(
        raw_event_consumer,
        "decode_message",
        lambda message, model_type: usage_event,
    )
    monkeypatch.setattr(raw_event_consumer, "publish_event", publish)
    monkeypatch.setattr(raw_event_consumer, "commit_message", commit)

    raw_event_consumer.process_raw_message(
        Mock(),
        Mock(),
        mapping_store,
        Mock(),
        Mock(),
        Mock(),
    )

    assert calls == ["publish", "commit"]
    assert publish.call_args.args[1] == raw_event_consumer.SCHEMA_DRIFT_TOPIC
    assert publish.call_args.args[2] == usage_event
    assert publish.call_args.args[3] == usage_event.case_id


def test_mapping_failure_publishes_failed_event_then_commits(
    usage_event: InternalUsageEvent,
    mapping_factory: Callable[..., RuntimeMapping],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    mapping_store = Mock()
    mapping_store.get.return_value = mapping_factory()
    mapping_engine = Mock()
    mapping_engine.apply_mapping.side_effect = MappingEngineError(
        "invalid mapping"
    )
    publish = Mock(
        side_effect=lambda producer, topic, event: calls.append("publish")
    )
    commit = Mock(side_effect=lambda consumer, message: calls.append("commit"))
    monkeypatch.setattr(
        raw_event_consumer,
        "decode_message",
        lambda message, model_type: usage_event,
    )
    monkeypatch.setattr(raw_event_consumer, "publish_event", publish)
    monkeypatch.setattr(raw_event_consumer, "commit_message", commit)

    raw_event_consumer.process_raw_message(
        Mock(),
        Mock(),
        mapping_store,
        mapping_engine,
        Mock(),
        Mock(),
    )

    assert calls == ["publish", "commit"]
    assert publish.call_args.args[1] == raw_event_consumer.FAILED_EVENTS_TOPIC
    failed_event = publish.call_args.args[2]
    assert isinstance(failed_event, FailedEvent)
    assert failed_event.event == usage_event
    assert failed_event.reason == "invalid mapping"


def test_duplicate_event_is_still_committed(
    usage_event: InternalUsageEvent,
    mapping_factory: Callable[..., RuntimeMapping],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapping_store = Mock()
    mapping_store.get.return_value = mapping_factory()
    mapping_engine = Mock()
    mapping_engine.apply_mapping.return_value = {
        "event_id": usage_event.event_id,
        "case_id": usage_event.case_id,
        "source": usage_event.source,
        "category": "cloud",
        "usage_type": "compute_time",
    }
    repository = Mock()
    repository.save.return_value = False
    commit = Mock()
    monkeypatch.setattr(
        raw_event_consumer,
        "decode_message",
        lambda message, model_type: usage_event,
    )
    monkeypatch.setattr(raw_event_consumer, "commit_message", commit)

    raw_event_consumer.process_raw_message(
        Mock(),
        Mock(),
        mapping_store,
        mapping_engine,
        repository,
        Mock(),
    )

    repository.save.assert_called_once()
    commit.assert_called_once()


def test_storage_error_does_not_commit(
    usage_event: InternalUsageEvent,
    mapping_factory: Callable[..., RuntimeMapping],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapping_store = Mock()
    mapping_store.get.return_value = mapping_factory()
    mapping_engine = Mock()
    mapping_engine.apply_mapping.return_value = {
        "event_id": usage_event.event_id,
        "case_id": usage_event.case_id,
        "source": usage_event.source,
        "category": "cloud",
        "usage_type": "compute_time",
    }
    repository = Mock()
    repository.save.side_effect = NormalizedEventStorageError("unavailable")
    commit = Mock()
    monkeypatch.setattr(
        raw_event_consumer,
        "decode_message",
        lambda message, model_type: usage_event,
    )
    monkeypatch.setattr(raw_event_consumer, "commit_message", commit)

    with pytest.raises(NormalizedEventStorageError):
        raw_event_consumer.process_raw_message(
            Mock(),
            Mock(),
            mapping_store,
            mapping_engine,
            repository,
            Mock(),
        )

    commit.assert_not_called()


def test_publish_error_does_not_commit(
    usage_event: InternalUsageEvent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapping_store = Mock()
    mapping_store.get.return_value = None
    commit = Mock()
    monkeypatch.setattr(
        raw_event_consumer,
        "decode_message",
        lambda message, model_type: usage_event,
    )
    monkeypatch.setattr(
        raw_event_consumer,
        "publish_event",
        Mock(side_effect=KafkaPublishError("unavailable")),
    )
    monkeypatch.setattr(raw_event_consumer, "commit_message", commit)

    with pytest.raises(KafkaPublishError):
        raw_event_consumer.process_raw_message(
            Mock(),
            Mock(),
            mapping_store,
            Mock(),
            Mock(),
            Mock(),
        )

    commit.assert_not_called()
