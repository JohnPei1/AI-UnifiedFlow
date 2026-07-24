"""Database models for normalized usage events."""

from datetime import datetime
from decimal import Decimal

from pydantic import JsonValue
from sqlalchemy import DateTime, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class NormalizedEventRecord(Base):
    __tablename__ = "normalized_events"
    __table_args__ = (
        Index("ix_normalized_events_usage_start", "usage_start"),
        Index(
            "ix_normalized_events_account_usage_start",
            "account_id",
            "usage_start",
        ),
        Index(
            "ix_normalized_events_project_usage_start",
            "project_id",
            "usage_start",
        ),
        Index(
            "ix_normalized_events_source_service_usage_start",
            "source",
            "service",
            "usage_start",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    case_id: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    service: Mapped[str | None] = mapped_column(String)
    resource: Mapped[str | None] = mapped_column(String)
    account_id: Mapped[str | None] = mapped_column(String)
    project_id: Mapped[str | None] = mapped_column(String)
    region: Mapped[str | None] = mapped_column(String)
    usage_type: Mapped[str] = mapped_column(String, nullable=False)
    input_units: Mapped[Decimal | None] = mapped_column(Numeric(30, 10))
    output_units: Mapped[Decimal | None] = mapped_column(Numeric(30, 10))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(30, 10))
    unit: Mapped[str | None] = mapped_column(String)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    currency: Mapped[str | None] = mapped_column(String)
    usage_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    usage_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    raw_payload: Mapped[dict[str, JsonValue]] = mapped_column(
        JSONB,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
