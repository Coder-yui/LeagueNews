from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
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
    aggregation_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    event_kind: Mapped[str] = mapped_column(String(50), default="other", index=True)
    aggregation_strategy: Mapped[str] = mapped_column(
        String(30), default="singleton"
    )
    product_scope: Mapped[str] = mapped_column(
        String(40), default="uncertain", index=True
    )
    lifecycle_status: Mapped[str] = mapped_column(
        String(40), default="developing", index=True
    )
    credibility_status: Mapped[str] = mapped_column(
        String(40), default="unverified", index=True
    )
    credibility_score: Mapped[float] = mapped_column(Float, default=0)
    importance_score: Mapped[float] = mapped_column(Float, default=0, index=True)
    importance_evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    importance_dimensions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    importance_policy_version: Mapped[str] = mapped_column(
        String(80), default="event-importance-v5-component-baselines"
    )
    latest_development: Mapped[str] = mapped_column(Text, default="")
    independent_source_count: Mapped[int] = mapped_column(Integer, default=0)
    supporting_source_count: Mapped[int] = mapped_column(Integer, default=0)
    contradicting_source_count: Mapped[int] = mapped_column(Integer, default=0)
    official_source_count: Mapped[int] = mapped_column(Integer, default=0)
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
            "credibility_score >= 0 AND credibility_score <= 1",
            name="ck_events_credibility_score",
        ),
        CheckConstraint(
            "importance_score >= 0 AND importance_score <= 1",
            name="ck_events_importance_score",
        ),
        CheckConstraint(
            "independent_source_count >= 0",
            name="ck_events_independent_source_count",
        ),
        CheckConstraint(
            "official_source_count >= 0",
            name="ck_events_official_source_count",
        ),
        CheckConstraint("supporting_source_count >= 0", name="ck_events_supporting_source_count"),
        CheckConstraint("contradicting_source_count >= 0", name="ck_events_contradicting_source_count"),
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
    membership_role: Mapped[str] = mapped_column(String(20), default="primary")
    evidence_stance: Mapped[str] = mapped_column(String(20), default="supports")
    independence_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_official_evidence: Mapped[bool] = mapped_column(Boolean, default=False)
    source_reliability_snapshot: Mapped[float] = mapped_column(Float, default=0.5)
    timeline_note: Mapped[str] = mapped_column(Text, default="")
    update_kind: Mapped[str] = mapped_column(String(30), default="new_fact")
    is_significant_update: Mapped[bool] = mapped_column(Boolean, default=True)
    importance_contribution: Mapped[float] = mapped_column(Float, default=0)
    importance_contribution_evidence: Mapped[list[str]] = mapped_column(
        JSON, default=list
    )
    source_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    membership_status: Mapped[str] = mapped_column(
        String(20), default="active", index=True
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    withdrawal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_correction_id: Mapped[int | None] = mapped_column(
        ForeignKey("pipeline_corrections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    event: Mapped[Event] = relationship(back_populates="messages")
    normalized_item: Mapped["NormalizedItem"] = relationship(  # noqa: F821
        back_populates="event_memberships"
    )

    __table_args__ = (
        CheckConstraint(
            "importance_contribution >= 0 AND importance_contribution <= 1",
            name="ck_event_messages_importance_contribution",
        ),
        CheckConstraint(
            "membership_role IN ('primary', 'component', 'cross_ref')",
            name="ck_event_messages_membership_role",
        ),
        CheckConstraint(
            "membership_status IN ('active', 'withdrawn')",
            name="ck_event_messages_membership_status",
        ),
        CheckConstraint(
            "evidence_stance IN ('supports', 'contradicts', 'context')",
            name="ck_event_messages_evidence_stance",
        ),
        CheckConstraint(
            "source_reliability_snapshot >= 0 AND source_reliability_snapshot <= 1",
            name="ck_event_messages_source_reliability_snapshot",
        ),
        CheckConstraint(
            "update_kind IN ('new_fact', 'confirmation', 'refutation', 'correction', "
            "'context', 'duplicate_evidence')",
            name="ck_event_messages_update_kind",
        ),
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
    execution_mode: Mapped[str] = mapped_column(String(20), default="manual", index=True)
    correction_id: Mapped[int | None] = mapped_column(
        ForeignKey("pipeline_corrections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    restart_from_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
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
    decision_source: Mapped[str] = mapped_column(String(20), default="manual", index=True)
    policy_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
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
