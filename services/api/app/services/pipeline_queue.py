from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.pipeline import PipelineJob


def enqueue_pipeline_job(
    db: Session,
    *,
    raw_item_id: int,
    correction_id: int | None = None,
    current_stage: str = "relevance",
) -> PipelineJob | None:
    if not settings.pipeline_automation_enabled and current_stage != "event_aggregation":
        return None
    existing = db.scalar(
        select(PipelineJob).where(
            PipelineJob.raw_item_id == raw_item_id,
            PipelineJob.status.in_(["queued", "running"]),
        )
    )
    if existing is not None:
        return existing
    job = PipelineJob(
        raw_item_id=raw_item_id,
        correction_id=correction_id,
        status="queued",
        current_stage=current_stage,
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
                PipelineJob.status.in_(["queued", "running"]),
            )
        )
