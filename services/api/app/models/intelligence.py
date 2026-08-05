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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    normalized_item_id: Mapped[int] = mapped_column(
        ForeignKey("normalized_items.id", ondelete="CASCADE"), index=True
    )
    subject: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    predicate: Mapped[str] = mapped_column(String(120))
    object_value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    before_value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after_value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    stance: Mapped[str] = mapped_column(String(20), default="asserts", index=True)
    claim_type: Mapped[str] = mapped_column(String(40), default="statement", index=True)
    temporal_role: Mapped[str] = mapped_column(
        String(20), default="state", index=True
    )
    attribution: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    supersedes_claim_id: Mapped[int | None] = mapped_column(
        ForeignKey("claims.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    extraction_model: Mapped[str] = mapped_column(String(120))
    schema_version: Mapped[str] = mapped_column(String(40), default="claim-v1")
    confidence: Mapped[float] = mapped_column(default=1.0)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    normalized_item: Mapped["NormalizedItem"] = relationship(  # noqa: F821
        back_populates="claims"
    )
    event_links: Mapped[list["EventClaim"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "stance IN ('asserts', 'supports', 'contradicts', 'context')",
            name="ck_claims_stance",
        ),
        CheckConstraint(
            "status IN ('active', 'superseded', 'withdrawn')",
            name="ck_claims_status",
        ),
        CheckConstraint(
            "temporal_role IN ('state', 'event', 'prediction')",
            name="ck_claims_temporal_role",
        ),
        CheckConstraint("revision >= 1", name="ck_claims_revision"),
    )


class EventClaim(Base):
    __tablename__ = "event_claims"

    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True
    )
    relation: Mapped[str] = mapped_column(String(20), default="supports")
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    claim: Mapped[Claim] = relationship(back_populates="event_links")


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(primary_key=True)
    digest_type: Mapped[str] = mapped_column(String(20), index=True)
    timezone: Mapped[str] = mapped_column(String(80))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    language: Mapped[str] = mapped_column(String(20), default="zh-CN")
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="published", index=True)
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    input_hash: Mapped[str] = mapped_column(String(64))
    input_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    generation_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    revisions: Mapped[list["DigestRevision"]] = relationship(
        back_populates="digest", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "digest_type", "timezone", "cutoff_at", name="uq_digests_window"
        ),
        CheckConstraint(
            "digest_type IN ('daily', 'weekly')", name="ck_digests_type"
        ),
        CheckConstraint(
            "status IN ('published', 'withdrawn')", name="ck_digests_status"
        ),
    )


class DigestRevision(Base):
    __tablename__ = "digest_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    digest_id: Mapped[int] = mapped_column(
        ForeignKey("digests.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(String(64))
    input_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    change_note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    digest: Mapped[Digest] = relationship(back_populates="revisions")

    __table_args__ = (
        UniqueConstraint("digest_id", "revision", name="uq_digest_revisions"),
        CheckConstraint("revision >= 1", name="ck_digest_revisions_revision"),
        Index("ix_digest_revisions_created_at", "created_at"),
    )
