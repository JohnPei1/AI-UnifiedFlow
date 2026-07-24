"""Create the FastAPI application and its shared resources."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import SUPPORTED_SOURCES
from app.db.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.db.repository import NormalizedEventRepository
from app.messaging.kafka_client import create_kafka_producer
from app.normalization.mapping_store import MappingStore


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    database_engine = create_database_engine()
    kafka_producer = None

    try:
        initialize_database(database_engine)

        mapping_store = MappingStore()
        mapping_store.initialize()

        kafka_producer = create_kafka_producer()

        app.state.database_engine = database_engine
        app.state.event_repository = NormalizedEventRepository(
            create_session_factory(database_engine)
        )
        app.state.mapping_store = mapping_store
        app.state.kafka_producer = kafka_producer
        app.state.supported_sources = SUPPORTED_SOURCES

        yield
    finally:
        if kafka_producer is not None:
            kafka_producer.flush(timeout=5)

        database_engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
