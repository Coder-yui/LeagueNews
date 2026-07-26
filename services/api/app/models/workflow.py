from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ProcessingRun(Base):
    __tablename__ = "processing_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_items.id", ondelete="CASCADE"), index=True
    )
    supersedes_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("processing_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    workflow_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(40), default="running", index=True)
    outcome: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    current_stage: Mapped[str] = mapped_column(String(40), index=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    raw_item: Mapped["RawItem"] = relationship(back_populates="processing_runs")  # noqa: F821
    reviews: Mapped[list["ReviewTask"]] = relationship(
        back_populates="processing_run", cascade="all, delete-orphan"
    )


class ReviewTask(Base):
    __tablename__ = "review_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    processing_run_id: Mapped[int] = mapped_column(
        ForeignKey("processing_runs.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    proposal: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    feedback: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    processing_run: Mapped[ProcessingRun] = relationship(back_populates="reviews")


class KnowledgeRule(Base):
    __tablename__ = "knowledge_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    knowledge_type: Mapped[str] = mapped_column(String(40), index=True)
    scope: Mapped[str] = mapped_column(String(160), default="global", index=True)
    rule_text: Mapped[str] = mapped_column(Text)
    correction_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_review_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_event_review_id: Mapped[int | None] = mapped_column(
        ForeignKey("event_review_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GlossaryTerm(Base):
    __tablename__ = "glossary_terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_term: Mapped[str] = mapped_column(String(255), index=True)
    preferred_translation: Mapped[str] = mapped_column(String(255))
    forbidden_translations: Mapped[list[str]] = mapped_column(JSON, default=list)
    scope: Mapped[str] = mapped_column(String(160), default="lol", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_review_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
