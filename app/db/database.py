"""Configure SQLite connections and database lifecycle."""

from pathlib import Path
from sqlite3 import Connection

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATABASE_PATH, SQLITE_BUSY_TIMEOUT_MS
from app.db.models import Base

SessionFactory = sessionmaker[Session]


class DatabaseConfigurationError(RuntimeError):
    pass


def create_database_engine(
    database_path: str | Path = DATABASE_PATH,
    busy_timeout_ms: int = SQLITE_BUSY_TIMEOUT_MS,
) -> Engine:
    """Create a SQLite engine with a busy timeout on every connection."""

    if (
        isinstance(busy_timeout_ms, bool)
        or not isinstance(busy_timeout_ms, int)
        or busy_timeout_ms < 0
    ):
        raise ValueError("busy timeout must be a non-negative integer")

    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path)),
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def set_busy_timeout(dbapi_connection: Connection, _: object) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        finally:
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> SessionFactory:
    return sessionmaker(bind=engine, expire_on_commit=False)


def initialize_database(engine: Engine) -> None:
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")

        Base.metadata.create_all(engine)
    except SQLAlchemyError as error:
        raise DatabaseConfigurationError(
            "could not initialize SQLite"
        ) from error


def is_database_healthy(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1
    except SQLAlchemyError:
        return False
