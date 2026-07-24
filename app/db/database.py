"""Configure PostgreSQL connections and database lifecycle."""

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATABASE_CONFIG, DatabaseSecrets
from app.db.models import Base

SessionFactory = sessionmaker[Session]


class DatabaseConfigurationError(RuntimeError):
    pass


def create_database_engine(
    database_url: str | URL | None = None,
) -> Engine:
    if database_url is None:
        database_url = URL.create(
            "postgresql+psycopg",
            username=DATABASE_CONFIG["user"],
            password=DatabaseSecrets().postgres_password.get_secret_value(),
            host=DATABASE_CONFIG["host"],
            port=DATABASE_CONFIG["port"],
            database=DATABASE_CONFIG["name"],
        )

    return create_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> SessionFactory:
    return sessionmaker(bind=engine, expire_on_commit=False)


def initialize_database(engine: Engine) -> None:
    try:
        Base.metadata.create_all(engine)
    except SQLAlchemyError as error:
        raise DatabaseConfigurationError(
            "could not initialize PostgreSQL"
        ) from error


def is_database_healthy(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1
    except SQLAlchemyError:
        return False
