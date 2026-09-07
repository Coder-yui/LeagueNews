from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import Any

from app.core.config import settings
from app.methods import MethodAssembly
from app.models.pipeline import PipelineJob
from app.models.raw_item import RawItem


def enqueue_pipeline_job(
    db: Session,
    *,
    raw_item_id: int,
    correction_id: int | None = None,
    current_stage: str = "relevance",
    job_type: str | None = None,
    target_entity_type: str | None = None,
    target_entity_id: int | None = None,
    target_revision: int | None = None,
    workflow_name: str | None = None,
    workflow_version: str | None = None,
    method_config: Any = None,
    max_attempts: int | None = None,
) -> PipelineJob | None:
    resolved_type = job_type or (
        "event" if current_stage == "event_aggregation" else "message"
    )
    if not settings.pipeline_automation_enabled and resolved_type != "event":
        return None
    existing = db.scalar(
        select(PipelineJob).where(
            PipelineJob.raw_item_id == raw_item_id,
            PipelineJob.job_type == resolved_type,
            or_(
                PipelineJob.status.in_(["queued", "running"]),
                and_(
                    PipelineJob.status == "failed",
                    PipelineJob.next_attempt_at.is_not(None),
                ),
            ),
        )
    )
    if existing is not None:
        return existing
    if target_revision is None:
        raw_item = db.get(RawItem, raw_item_id)
        target_revision = int(getattr(raw_item, "revision", 1) or 1)
    resolved_workflow = workflow_name or (
        "event_aggregation" if resolved_type == "event" else "item_processing"
    )
    resolved_version = workflow_version or "v3.0.0-dev2"
    if hasattr(method_config, "model_dump"):
        serialized_method_config = method_config.model_dump(mode="json")
    else:
        serialized_method_config = dict(method_config) if method_config is not None else MethodAssembly().config.model_dump(mode="json")
    job = PipelineJob(
        raw_item_id=raw_item_id,
        correction_id=correction_id,
        status="queued",
        current_stage=current_stage,
        job_type=resolved_type,
        target_entity_type=target_entity_type or (
            "normalized_item" if resolved_type == "event" else "raw_item"
        ),
        target_entity_id=target_entity_id or raw_item_id,
        target_revision=target_revision,
        workflow_name=resolved_workflow,
        workflow_version=resolved_version,
        method_config=serialized_method_config,
        max_attempts=max_attempts or settings.pipeline_worker_max_attempts,
    )
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
        return job
    except IntegrityError:
        return db.scalar(
            select(PipelineJob).where(
                PipelineJob.raw_item_id == raw_item_id,
                PipelineJob.job_type == resolved_type,
                or_(
                    PipelineJob.status.in_(["queued", "running"]),
                    and_(
                        PipelineJob.status == "failed",
                        PipelineJob.next_attempt_at.is_not(None),
                    ),
                ),
            )
        )
