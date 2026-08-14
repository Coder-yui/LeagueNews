from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class NotificationOutbox(Base):
    """Durable, provider-agnostic notification delivery record."""

    __tablename__ = "notification_outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    target: Mapped[str] = mapped_column(String(30), index=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    __table_args__ = (
        CheckConstraint(
            "target IN ('featured', 'alert')",
            name="ck_notification_outbox_target",
        ),
        CheckConstraint(
            "kind IN ('featured_message', 'collection_failure', 'pipeline_failure')",
            name="ck_notification_outbox_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed')",
            name="ck_notification_outbox_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_notification_outbox_attempts"),
        Index(
            "ix_notification_outbox_claimable",
            "status",
            "next_attempt_at",
        ),
    )
