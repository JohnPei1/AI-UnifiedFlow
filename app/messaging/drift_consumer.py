"""Generate mappings for schema-drift events."""

from confluent_kafka import Consumer, Message, Producer
from pydantic import ValidationError

from app.ai.client import AIClient, AIResponseError
from app.ai.security import AISecurityError
from app.config import AI_STATIC_FIELDS
from app.messaging.kafka_client import (
    FAILED_EVENTS_TOPIC,
    RAW_EVENTS_TOPIC,
    SCHEMA_DRIFT_TOPIC,
    commit_message,
    create_kafka_consumer,
    create_kafka_producer,
    decode_message,
    is_unknown_topic_message,
    publish_event,
)
from app.normalization.mapping_engine import MappingEngine, MappingEngineError
from app.normalization.mapping_store import MappingStore
from app.schemas.mappings import RuntimeMapping
from app.schemas.requests import FailedEvent, InternalUsageEvent

SCHEMA_DRIFT_CONSUMER_GROUP_ID = "schema-drift-consumer"


def process_drift_message(
    consumer: Consumer,
    producer: Producer,
    mapping_store: MappingStore,
    mapping_engine: MappingEngine,
    ai_client: AIClient,
    message: Message,
) -> None:
    """Resolve one schema-drift event and commit after its outcome is published."""

    event = decode_message(message, InternalUsageEvent)
    mapping = mapping_store.get(event.case_id)

    if mapping is not None:
        publish_event(
            producer,
            topic=RAW_EVENTS_TOPIC,
            event=event,
        )
        commit_message(consumer, message)
        return

    # Generate and apply a mapping proposal from the AI model
    try:
        proposal = ai_client.generate_mapping(event.source, event.payload)
        mapping = RuntimeMapping(
            case_id=event.case_id,
            source=event.source,
            schema_fingerprint=event.schema_fingerprint,
            version=1,
            created_by="ai",
            static_fields=AI_STATIC_FIELDS[event.source],
            fields=proposal.fields,
        )
        mapping_engine.apply_mapping(event, mapping)
    except (
        AISecurityError,
        AIResponseError,
        MappingEngineError,
        ValidationError,
    ) as error:
        # Publish a failed event and commit the message to avoid reprocessing
        publish_event(
            producer,
            topic=FAILED_EVENTS_TOPIC,
            event=FailedEvent(event=event, reason=str(error)),
        )
        commit_message(consumer, message)
        return

    # Save the mapping and publish the event back to raw-events after successful processing
    mapping_store.save(mapping)
    publish_event(
        producer,
        topic=RAW_EVENTS_TOPIC,
        event=event,
    )
    commit_message(consumer, message)


def run() -> None:
    producer = None
    consumer = None

    try:
        mapping_store = MappingStore()
        mapping_store.initialize()

        mapping_engine = MappingEngine()
        ai_client = AIClient()
        producer = create_kafka_producer()
        consumer = create_kafka_consumer(
            SCHEMA_DRIFT_CONSUMER_GROUP_ID,
            [SCHEMA_DRIFT_TOPIC],
        )

        while True:
            message = consumer.poll(1.0)
            if message is None:
                continue

            # The raw-event consumer creates this topic on the first drift.
            if is_unknown_topic_message(message):
                continue

            process_drift_message(
                consumer,
                producer,
                mapping_store,
                mapping_engine,
                ai_client,
                message,
            )
    except KeyboardInterrupt:
        pass
    finally:
        if consumer is not None:
            consumer.close()
        if producer is not None:
            producer.flush(timeout=5)


if __name__ == "__main__":
    run()
