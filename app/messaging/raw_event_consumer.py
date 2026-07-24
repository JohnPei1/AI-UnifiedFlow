"""Consume raw events and route them through normalization."""

from confluent_kafka import Consumer, Message, Producer

from app.db.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.db.service import NormalizedEventRepository
from app.messaging.kafka_client import (
    FAILED_EVENTS_TOPIC,
    RAW_EVENTS_TOPIC,
    SCHEMA_DRIFT_TOPIC,
    commit_message,
    create_kafka_consumer,
    create_kafka_producer,
    decode_message,
    publish_event,
)
from app.normalization.mapping_engine import MappingEngine, MappingEngineError
from app.normalization.mapping_store import MappingStore
from app.schemas.requests import FailedEvent, InternalUsageEvent

RAW_EVENT_CONSUMER_GROUP_ID = "raw-event-consumer"


def process_raw_message(
    consumer: Consumer,
    producer: Producer,
    mapping_store: MappingStore,
    mapping_engine: MappingEngine,
    repository: NormalizedEventRepository,
    message: Message,
) -> None:
    """Process one raw event and commit only after its outcome is stored."""

    event = decode_message(message, InternalUsageEvent)
    mapping = mapping_store.get(event.case_id)

    if mapping is None:
        publish_event(
            producer,
            SCHEMA_DRIFT_TOPIC,
            event,
            event.case_id,
        )
        commit_message(consumer, message)
        return

    try:
        normalized_event = mapping_engine.apply_mapping(event, mapping)
    except MappingEngineError as error:
        publish_event(
            producer,
            FAILED_EVENTS_TOPIC,
            FailedEvent(event=event, reason=str(error)),
        )
        commit_message(consumer, message)
        return

    repository.save(normalized_event)
    commit_message(consumer, message)


def run() -> None:
    database_engine = create_database_engine()
    consumer = None
    producer = None

    try:
        initialize_database(database_engine)

        mapping_store = MappingStore()
        mapping_store.initialize()

        mapping_engine = MappingEngine()
        repository = NormalizedEventRepository(
            create_session_factory(database_engine)
        )
        producer = create_kafka_producer() # Publish to schema-drift or failed-events topics
        consumer = create_kafka_consumer(
            RAW_EVENT_CONSUMER_GROUP_ID,
            [RAW_EVENTS_TOPIC],
        ) # Consumer from raw-events topic

        while True:
            message = consumer.poll(1.0)
            if message is not None:
                process_raw_message(
                    consumer,
                    producer,
                    mapping_store,
                    mapping_engine,
                    repository,
                    message,
                )
    finally:
        if consumer is not None:
            consumer.close()
        if producer is not None:
            producer.flush(timeout=5)
        database_engine.dispose()


if __name__ == "__main__":
    run()
