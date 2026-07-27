from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SourceCollectionSchedule(Base):
    __tablename__ = "source_collection_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    retry_delay_minutes: Mapped[int] = mapped_column(Integer, default=15)
    fetch_limit: Mapped[int] = mapped_column(Integer, default=10)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    run_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_connector_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("connector_runs.id", ondelete="SET NULL"), nullable=True
    )
    last_status: Mapped[str] = mapped_column(String(30), default="idle", index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    source: Mapped["Source"] = relationship(back_populates="collection_schedule")  # noqa: F821
    last_connector_run: Mapped["ConnectorRun | None"] = relationship()  # noqa: F821

    @property
    def source_name(self) -> str:
        return self.source.name

    @property
    def connector_type(self) -> str:
        return self.source.connector_type

    __table_args__ = (
        UniqueConstraint(
            "source_id",
            name="uq_source_collection_schedules_source_id",
        ),
        CheckConstraint(
            "interval_minutes >= 5 AND interval_minutes <= 10080",
            name="ck_source_collection_schedules_interval",
        ),
        CheckConstraint(
            "retry_delay_minutes >= 1 AND retry_delay_minutes <= 1440",
            name="ck_source_collection_schedules_retry_delay",
        ),
        CheckConstraint(
            "fetch_limit >= 1 AND fetch_limit <= 50",
            name="ck_source_collection_schedules_fetch_limit",
        ),
        CheckConstraint(
            "last_status IN ('idle', 'running', 'succeeded', 'failed')",
            name="ck_source_collection_schedules_status",
        ),
    )
