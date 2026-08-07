from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PipelineCorrection(Base):
    __tablename__ = "pipeline_corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_items.id", ondelete="RESTRICT"), index=True
    )
    normalized_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("normalized_items.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    original_event_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    source_processing_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("processing_runs.id", ondelete="SET NULL"), nullable=True
    )
    source_event_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("event_aggregation_runs.id", ondelete="SET NULL"), nullable=True
    )
    checkpoint_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "processing_checkpoints.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_pipeline_corrections_checkpoint_id",
        ),
        nullable=True,
    )
    restart_from_stage: Mapped[str] = mapped_column(String(40), index=True)
    resume_mode: Mapped[str] = mapped_column(String(20), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="requested", index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checkpoints: Mapped[list["ProcessingCheckpoint"]] = relationship(
        back_populates="correction",
        foreign_keys="ProcessingCheckpoint.correction_id",
    )

    __table_args__ = (
        CheckConstraint(
            "restart_from_stage IN "
            "('relevance', 'image_ocr', 'translation', 'fact_classify', "
            "'importance', 'claim_gen', "
            "'event_decision')",
            name="ck_pipeline_corrections_restart_stage",
        ),
        CheckConstraint(
            "resume_mode IN ('manual', 'automatic')",
            name="ck_pipeline_corrections_resume_mode",
        ),
        CheckConstraint(
            "status IN ('requested', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_pipeline_corrections_status",
        ),
    )


class ProcessingCheckpoint(Base):
    __tablename__ = "processing_checkpoints"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_items.id", ondelete="RESTRICT"), index=True
    )
    normalized_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("normalized_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    processing_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("processing_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_aggregation_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("event_aggregation_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    correction_id: Mapped[int | None] = mapped_column(
        ForeignKey("pipeline_corrections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(40), index=True)
    output_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    artifact_references: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    knowledge_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decision_source: Mapped[str] = mapped_column(String(20), default="manual")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    invalidation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    correction: Mapped[PipelineCorrection | None] = relationship(
        back_populates="checkpoints",
        foreign_keys=[correction_id],
    )

    __table_args__ = (
        CheckConstraint(
            "stage IN "
            "('relevance', 'image_ocr', 'translation', 'fact_classify', "
            "'importance', 'claim_gen', "
            "'event_decision')",
            name="ck_processing_checkpoints_stage",
        ),
        CheckConstraint(
            "decision_source IN ('manual', 'automatic', 'system')",
            name="ck_processing_checkpoints_decision_source",
        ),
    )


class PipelineJob(Base):
    __tablename__ = "pipeline_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_items.id", ondelete="RESTRICT"), index=True
    )
    correction_id: Mapped[int | None] = mapped_column(
        ForeignKey("pipeline_corrections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    current_stage: Mapped[str] = mapped_column(
        String(40), default="relevance", index=True
    )
    processing_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("processing_runs.id", ondelete="SET NULL"), nullable=True
    )
    event_aggregation_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("event_aggregation_runs.id", ondelete="SET NULL"), nullable=True
    )
    last_checkpoint_id: Mapped[int | None] = mapped_column(
        ForeignKey("processing_checkpoints.id", ondelete="SET NULL"), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    worker_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recovery_count: Mapped[int] = mapped_column(Integer, default=0)
    recovery_provenance: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_pipeline_jobs_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_pipeline_jobs_attempts"),
        CheckConstraint("recovery_count >= 0", name="ck_pipeline_jobs_recovery_count"),
        Index(
            "uq_pipeline_jobs_active_raw_item",
            "raw_item_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )
