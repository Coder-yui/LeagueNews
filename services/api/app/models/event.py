from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str | None] = mapped_column(
        String(160), nullable=True, unique=True, index=True
    )
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    first_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["EventMessage"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    revisions: Mapped[list["EventRevision"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="EventRevision.revision",
    )

    __table_args__ = (
        CheckConstraint("current_revision >= 1", name="ck_events_current_revision_positive"),
        CheckConstraint(
            "first_published_at IS NULL OR last_published_at IS NULL "
            "OR first_published_at <= last_published_at",
            name="ck_events_publish_range",
        ),
    )


class EventMessage(Base):
    __tablename__ = "event_messages"

    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    normalized_item_id: Mapped[int] = mapped_column(
        ForeignKey("normalized_items.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    relation_type: Mapped[str] = mapped_column(String(30), default="primary")
    source_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped[Event] = relationship(back_populates="messages")
    normalized_item: Mapped["NormalizedItem"] = relationship(  # noqa: F821
        back_populates="event_membership"
    )

    __table_args__ = (
        UniqueConstraint("normalized_item_id", name="uq_event_messages_normalized_item"),
        CheckConstraint("relation_type = 'primary'", name="ck_event_messages_relation_type"),
    )


class EventRevision(Base):
    __tablename__ = "event_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text)
    change_note: Mapped[str] = mapped_column(Text)
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped[Event] = relationship(back_populates="revisions")

    __table_args__ = (
        UniqueConstraint("event_id", "revision", name="uq_event_revisions_event_revision"),
        CheckConstraint("revision >= 1", name="ck_event_revisions_revision_positive"),
    )


class EventAggregationRun(Base):
    __tablename__ = "event_aggregation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    normalized_item_id: Mapped[int] = mapped_column(
        ForeignKey("normalized_items.id", ondelete="RESTRICT"), index=True
    )
    supersedes_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("event_aggregation_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(40), default="running", index=True)
    outcome: Mapped[str | None] = mapped_column(String(40), nullable=True)
    current_stage: Mapped[str] = mapped_column(String(40), default="event_decision")
    candidate_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    decision_draft: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    normalized_item: Mapped["NormalizedItem"] = relationship()  # noqa: F821
    reviews: Mapped[list["EventReviewTask"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index(
            "uq_event_aggregation_runs_active_item",
            "normalized_item_id",
            unique=True,
            postgresql_where=text("status IN ('running', 'awaiting_review')"),
            sqlite_where=text("status IN ('running', 'awaiting_review')"),
        ),
    )


class EventReviewTask(Base):
    __tablename__ = "event_review_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_aggregation_run_id: Mapped[int] = mapped_column(
        ForeignKey("event_aggregation_runs.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    proposal: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    feedback: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[EventAggregationRun] = relationship(back_populates="reviews")

    __table_args__ = (
        Index("ix_event_review_tasks_run_id", "event_aggregation_run_id"),
        Index(
            "uq_event_review_tasks_pending_run",
            "event_aggregation_run_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )
