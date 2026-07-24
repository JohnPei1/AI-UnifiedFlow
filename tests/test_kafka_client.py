from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.messaging import kafka_client
from app.messaging.kafka_client import (
    KafkaClientError,
    KafkaMessageError,
    KafkaPublishError,
)
from app.schemas.requests import EventAcceptedResponse


class FakeMessage:
    def __init__(
        self,
        value: bytes | None = None,
        error: object | None = None,
    ) -> None:
        self._value = value
        self._error = error

    def value(self) -> bytes | None:
        return self._value

    def error(self) -> object | None:
        return self._error


class FakeProducer:
    def __init__(
        self,
        acknowledge: bool = True,
        delivery_error: object | None = None,
        remaining: int = 0,
    ) -> None:
        self.acknowledge = acknowledge
        self.delivery_error = delivery_error
        self.remaining = remaining
        self.produced: dict[str, object] | None = None

    def produce(self, **kwargs: object) -> None:
        self.produced = kwargs
        if self.acknowledge:
            kwargs["on_delivery"](
                self.delivery_error,
                FakeMessage(),
            )

    def flush(self) -> int:
        return self.remaining


def event() -> EventAcceptedResponse:
    return EventAcceptedResponse(event_id="evt_1", case_id="case_1")


def test_publish_serializes_event_and_key() -> None:
    producer = FakeProducer()

    kafka_client.publish_event(
        producer,
        "raw-events",
        event(),
        "case_1",
    )

    assert producer.produced["topic"] == "raw-events"
    assert producer.produced["key"] == b"case_1"
    assert b'"event_id":"evt_1"' in producer.produced["value"]


def test_publish_requires_delivery_acknowledgement() -> None:
    producer = FakeProducer(acknowledge=False)

    with pytest.raises(KafkaPublishError, match="acknowledgement"):
        kafka_client.publish_event(producer, "raw-events", event())


def test_publish_rejects_delivery_error() -> None:
    producer = FakeProducer(delivery_error="broker rejected message")

    with pytest.raises(KafkaPublishError, match="broker rejected"):
        kafka_client.publish_event(producer, "raw-events", event())


def test_publish_rejects_delivery_timeout() -> None:
    producer = FakeProducer(remaining=1)

    with pytest.raises(KafkaPublishError, match="timed out"):
        kafka_client.publish_event(producer, "raw-events", event())


def test_decode_message_validates_model() -> None:
    message = FakeMessage(event().model_dump_json().encode())

    decoded = kafka_client.decode_message(
        message,
        EventAcceptedResponse,
    )

    assert decoded == event()


@pytest.mark.parametrize(
    ("message", "error"),
    [
        (FakeMessage(error="partition error"), "message error"),
        (FakeMessage(value=None), "no value"),
        (FakeMessage(value=b"not json"), "message is invalid"),
    ],
)
def test_decode_rejects_invalid_messages(
    message: FakeMessage,
    error: str,
) -> None:
    with pytest.raises(KafkaMessageError, match=error):
        kafka_client.decode_message(message, EventAcceptedResponse)


def test_commit_is_synchronous() -> None:
    consumer = Mock()
    consumer.commit.return_value = []
    message = FakeMessage()

    kafka_client.commit_message(consumer, message)

    consumer.commit.assert_called_once_with(
        message=message,
        asynchronous=False,
    )


def test_commit_rejects_partition_error() -> None:
    consumer = Mock()
    consumer.commit.return_value = [
        SimpleNamespace(error="commit failed")
    ]

    with pytest.raises(KafkaClientError, match="commit failed"):
        kafka_client.commit_message(consumer, FakeMessage())


def test_health_uses_broker_metadata() -> None:
    healthy = Mock()
    healthy.list_topics.return_value = SimpleNamespace(
        brokers={1: object()}
    )
    unhealthy = Mock()
    unhealthy.list_topics.return_value = SimpleNamespace(brokers={})

    assert kafka_client.is_kafka_healthy(healthy) is True
    assert kafka_client.is_kafka_healthy(unhealthy) is False


def test_consumer_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}

    class Consumer:
        def __init__(self, config: dict[str, object]) -> None:
            created["config"] = config

        def subscribe(self, topics: list[str]) -> None:
            created["topics"] = topics

    monkeypatch.setattr(kafka_client, "Consumer", Consumer)

    consumer = kafka_client.create_kafka_consumer(
        "worker",
        ["raw-events"],
    )

    assert isinstance(consumer, Consumer)
    assert created["topics"] == ["raw-events"]
    assert created["config"]["enable.auto.commit"] is False
    assert created["config"]["auto.offset.reset"] == "earliest"
