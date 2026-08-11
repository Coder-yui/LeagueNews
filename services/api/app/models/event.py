from datetime import datetime
from typing import Any

from sqlalchemy import (
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
from app.domain.event_types import (
    AGGREGATION_POLICY_VERSION,
    CREDIBILITY_POLICY_VERSION,
    HEAT_POLICY_VERSION,
    IMPORTANCE_POLICY_VERSION,
)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    aggregation_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    title: Mapped[str] = mapped_column(String(500))
    current_summary: Mapped[str] = mapped_column("summary", Text)
    event_family: Mapped[str] = mapped_column(
        String(50), default="other_named_development", index=True
    )
    products: Mapped[list[str]] = mapped_column(JSON, default=list)
    canonical_anchors: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    latest_development: Mapped[str] = mapped_column(Text, default="")
    key_facts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    unresolved_points: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    lifecycle_status: Mapped[str] = mapped_column(
        String(40), default="developing", index=True
    )
    first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_material_update_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    importance_score: Mapped[float] = mapped_column(Float, default=0, index=True)
    importance_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    importance_policy_version: Mapped[str] = mapped_column(
        String(80), default=IMPORTANCE_POLICY_VERSION
    )

    credibility_score: Mapped[float] = mapped_column(Float, default=0)
    credibility_level: Mapped[str] = mapped_column(
        String(40), default="unverified", index=True
    )
    credibility_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    credibility_policy_version: Mapped[str] = mapped_column(
        String(80), default=CREDIBILITY_POLICY_VERSION
    )
    independent_source_count: Mapped[int] = mapped_column(Integer, default=0)
    supporting_source_count: Mapped[int] = mapped_column(Integer, default=0)
    contradicting_source_count: Mapped[int] = mapped_column(Integer, default=0)
    official_source_count: Mapped[int] = mapped_column(Integer, default=0)

    heat_score: Mapped[float] = mapped_column(Float, default=0, index=True)
    heat_calculated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heat_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    heat_policy_version: Mapped[str] = mapped_column(String(80), default=HEAT_POLICY_VERSION)
    message_count_total: Mapped[int] = mapped_column(Integer, default=0)
    message_count_24h: Mapped[int] = mapped_column(Integer, default=0)
    unique_sources_24h: Mapped[int] = mapped_column(Integer, default=0)

    origin_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("normalized_items.id", ondelete="SET NULL"), nullable=True
    )
    primary_source_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("normalized_items.id", ondelete="SET NULL"), nullable=True
    )
    latest_update_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("normalized_items.id", ondelete="SET NULL"), nullable=True
    )
    best_media_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("normalized_items.id", ondelete="SET NULL"), nullable=True
    )

    aggregation_policy_version: Mapped[str] = mapped_column(
        String(80), default=AGGREGATION_POLICY_VERSION
    )
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    mentions: Mapped[list["EventMention"]] = relationship(
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
            "event_family IN ('gameplay_balance', 'gameplay_release', "
            "'cosmetic_release', 'player_activity', 'commercial_offer', "
            "'service_incident', 'security_enforcement', 'esports_match', "
            "'esports_schedule', 'roster_change', 'esports_rules', "
            "'universe_release', 'media_release', 'corporate_change', "
            "'platform_service', 'other_named_development')",
            name="ck_events_family",
        ),
        CheckConstraint(
            "lifecycle_status IN ('unconfirmed', 'developing', 'confirmed', "
            "'disputed', 'denied', 'resolved', 'stale')",
            name="ck_events_lifecycle",
        ),
        CheckConstraint(
            "credibility_level IN ('unverified', 'plausible', 'corroborated', "
            "'officially_confirmed', 'disputed', 'denied')",
            name="ck_events_credibility_level",
        ),
        CheckConstraint(
            "credibility_score >= 0 AND credibility_score <= 1",
            name="ck_events_credibility_score",
        ),
        CheckConstraint(
            "importance_score >= 0 AND importance_score <= 1",
            name="ck_events_importance_score",
        ),
        CheckConstraint("heat_score >= 0 AND heat_score <= 1", name="ck_events_heat_score"),
        CheckConstraint(
            "independent_source_count >= 0 AND supporting_source_count >= 0 "
            "AND contradicting_source_count >= 0 AND official_source_count >= 0",
            name="ck_events_evidence_counts",
        ),
        CheckConstraint(
            "message_count_total >= 0 AND message_count_24h >= 0 "
            "AND unique_sources_24h >= 0",
            name="ck_events_message_counts",
        ),
        CheckConstraint(
            "first_seen_at IS NULL OR last_seen_at IS NULL "
            "OR first_seen_at <= last_seen_at",
            name="ck_events_publish_range",
        ),
    )


class EventMention(Base):
    __tablename__ = "event_mentions"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    normalized_item_id: Mapped[int] = mapped_column(
        ForeignKey("normalized_items.id", ondelete="RESTRICT"), index=True
    )
    normalized_item_revision: Mapped[int] = mapped_column(Integer, default=1)
    mention_index: Mapped[int] = mapped_column(Integer)
    aggregation_policy_version: Mapped[str] = mapped_column(
        String(80), default=AGGREGATION_POLICY_VERSION
    )

    relation: Mapped[str] = mapped_column(String(30), default="reports")
    source_role: Mapped[str] = mapped_column(String(40), default="unknown")
    independence_group: Mapped[str | None] = mapped_column(String(500), nullable=True)
    materiality: Mapped[str] = mapped_column(String(30), default="material_update")
    evidence_excerpt: Mapped[str] = mapped_column(Text, default="")
    structured_fact_changes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    impact_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    source_reliability_snapshot: Mapped[float] = mapped_column(Float, default=0.5)
    source_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped[Event] = relationship(back_populates="mentions")
    normalized_item: Mapped["NormalizedItem"] = relationship(  # noqa: F821
        back_populates="event_mentions"
    )

    __table_args__ = (
        UniqueConstraint(
            "normalized_item_id",
            "normalized_item_revision",
            "mention_index",
            "aggregation_policy_version",
            name="uq_event_mentions_item_index_policy",
        ),
        CheckConstraint(
            "normalized_item_revision >= 1",
            name="ck_event_mentions_item_revision",
        ),
        CheckConstraint("mention_index >= 0", name="ck_event_mentions_index_nonnegative"),
        CheckConstraint(
            "relation IN ('reports', 'supports', 'confirms', 'denies', 'corrects', "
            "'mentions')",
            name="ck_event_mentions_relation",
        ),
        CheckConstraint(
            "source_role IN ('responsible_official', 'direct_subject', "
            "'first_party_participant', 'independent_media', 'known_leaker', "
            "'ordinary_account', 'republisher', 'unknown')",
            name="ck_event_mentions_source_role",
        ),
        CheckConstraint(
            "materiality IN ('material_update', 'corroboration_only', 'duplicate', "
            "'context_only')",
            name="ck_event_mentions_materiality",
        ),
        CheckConstraint(
            "source_reliability_snapshot >= 0 AND source_reliability_snapshot <= 1",
            name="ck_event_mentions_source_reliability",
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
    normalized_item_revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(40), default="running", index=True)
    outcome: Mapped[str | None] = mapped_column(String(40), nullable=True)
    current_stage: Mapped[str] = mapped_column(String(40), default="admission")
    admission_decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    aggregation_policy_version: Mapped[str] = mapped_column(
        String(80), default=AGGREGATION_POLICY_VERSION
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_call_count: Mapped[int] = mapped_column(Integer, default=0)
    candidate_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    decision_draft: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("normalized_item_revision >= 1", name="ck_event_runs_item_revision"),
        CheckConstraint("model_call_count >= 0", name="ck_event_runs_model_call_count"),
        Index(
            "uq_event_aggregation_runs_active_item",
            "normalized_item_id",
            unique=True,
            postgresql_where=text("status IN ('running', 'awaiting_review')"),
            sqlite_where=text("status IN ('running', 'awaiting_review')"),
        ),
    )
