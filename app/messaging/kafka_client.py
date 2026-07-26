"""Create Kafka clients and exchange validated event messages."""

from typing import TypeVar

from confluent_kafka import (
    Consumer,
    KafkaError,
    KafkaException,
    Message,
    Producer,
)
from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticSerializationError

from app.config import (
    KAFKA_BOOTSTRAP_SERVERS,
    KAFKA_CONSUMER_MAX_POLL_INTERVAL_MS,
    KAFKA_DELIVERY_TIMEOUT_MS,
    KAFKA_HEALTH_TIMEOUT_SECONDS,
)

RAW_EVENTS_TOPIC = "raw-events"
SCHEMA_DRIFT_TOPIC = "schema-drift"
FAILED_EVENTS_TOPIC = "failed-events"

T = TypeVar("T", bound=BaseModel)


class KafkaClientError(RuntimeError):
    pass


class KafkaPublishError(KafkaClientError):
    pass


class KafkaMessageError(KafkaClientError):
    pass


def create_kafka_producer() -> Producer:
    try:
        return Producer(
            {
                "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
                "delivery.timeout.ms": KAFKA_DELIVERY_TIMEOUT_MS,
            }
        )
    except (KafkaException, ValueError) as error:
        raise KafkaClientError(f"could not create Kafka producer: {error}") from error


def create_kafka_consumer(
    group_id: str,
    topics: list[str],
) -> Consumer:
    """Create a consumer (manual commit) subscribed to the given topics."""

    if not isinstance(group_id, str) or not group_id.strip():
        raise KafkaClientError("Kafka consumer group ID must not be blank")
    if not topics or any(
        not isinstance(topic, str) or not topic.strip() for topic in topics
    ):
        raise KafkaClientError("Kafka consumer topics must not be empty or blank")

    try:
        consumer = Consumer(
            {
                "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
                "group.id": group_id,
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
                "max.poll.interval.ms": KAFKA_CONSUMER_MAX_POLL_INTERVAL_MS,
            }
        )
        consumer.subscribe(topics)
        return consumer
    except (KafkaException, ValueError) as error:
        if "consumer" in locals():
            consumer.close()
        raise KafkaClientError(f"could not create Kafka consumer: {error}") from error


def publish_event(
    producer: Producer,
    topic: str,
    event: BaseModel,
    key: str | None = None,
) -> None:
    """Publish a Pydantic model and wait for Kafka delivery acknowledgement."""

    if not isinstance(topic, str) or not topic.strip():
        raise KafkaPublishError("Kafka topic must not be blank")
    if key is not None and not isinstance(key, str):
        raise KafkaPublishError("Kafka message key must be a string or null")

    acknowledged = False
    delivery_error: KafkaError | None = None

    def on_delivery(error: KafkaError | None, _: Message) -> None:
        nonlocal acknowledged, delivery_error
        acknowledged = True
        delivery_error = error

    try:
        value = event.model_dump_json(by_alias=True).encode("utf-8")
        encoded_key = key.encode("utf-8") if key is not None else None
        producer.produce(
            topic=topic,
            value=value,
            key=encoded_key,
            on_delivery=on_delivery,
        )
        remaining = producer.flush()
    except (
        BufferError,
        KafkaException,
        PydanticSerializationError,
        TypeError,
        ValueError,
    ) as error:
        raise KafkaPublishError(
            "could not publish Kafka event"
        ) from error

    if remaining:
        raise KafkaPublishError(
            f"Kafka delivery timed out with {remaining} message(s) pending"
        )

    if not acknowledged:
        raise KafkaPublishError(
            "Kafka delivery acknowledgement was not received"
        )

    if delivery_error is not None:
        raise KafkaPublishError(
            f"Kafka rejected the event: {delivery_error}"
        )

def decode_message(
    message: Message,
    model_type: type[T],
) -> T:
    """Decode a Kafka value and validate it as the requested Pydantic model."""

    message_error = message.error()
    if message_error is not None:
        raise KafkaMessageError(f"Kafka message error: {message_error}")

    value = message.value()
    if value is None:
        raise KafkaMessageError("Kafka message has no value")

    try:
        return model_type.model_validate_json(value)
    except (ValidationError, ValueError, TypeError) as error:
        raise KafkaMessageError(f"Kafka message is invalid: {error}") from error


def is_unknown_topic_message(message: Message) -> bool:
    error = message.error()
    return (
        error is not None
        and error.code() == KafkaError.UNKNOWN_TOPIC_OR_PART
    )


def commit_message(
    consumer: Consumer,
    message: Message,
) -> None:
    """Synchronously commit the offset immediately after the given message."""

    try:
        committed_offsets = consumer.commit(
            message=message,
            asynchronous=False,
        )
    except KafkaException as error:
        raise KafkaClientError(f"could not commit Kafka message: {error}") from error

    for offset in committed_offsets or []:
        if offset.error is not None:
            raise KafkaClientError(
                f"could not commit Kafka message: {offset.error}"
            )

def is_kafka_healthy(producer: Producer) -> bool:
    try:
        metadata = producer.list_topics(timeout=KAFKA_HEALTH_TIMEOUT_SECONDS)
        return bool(metadata.brokers)
    except KafkaException:
        return False
