import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.event import EventAggregationRun, EventReviewTask
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineCorrection, PipelineJob, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.workflow import ProcessingRun, ReviewTask
from app.workflows.event_aggregation import (
    approve_event_review,
    start_event_aggregation,
)
from app.workflows.reviewed_pipeline import approve_review, start_item_processing


def enqueue_pipeline_job(
    db: Session,
    *,
    raw_item_id: int,
    correction_id: int | None = None,
    current_stage: str = "relevance",
) -> PipelineJob | None:
    if not settings.pipeline_automation_enabled:
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
    db.add(job)
    return job


def enqueue_pending_raw_items(db: Session) -> list[PipelineJob]:
    raw_items = list(
        db.scalars(
            select(RawItem)
            .outerjoin(NormalizedItem, NormalizedItem.raw_item_id == RawItem.id)
            .outerjoin(ProcessingRun, ProcessingRun.raw_item_id == RawItem.id)
            .where(
                NormalizedItem.id.is_(None),
                ProcessingRun.id.is_(None),
            )
            .order_by(RawItem.ingested_at, RawItem.id)
        )
    )
    jobs = [
        job
        for raw_item in raw_items
        if (
            job := enqueue_pipeline_job(
                db,
                raw_item_id=raw_item.id,
            )
        )
        is not None
    ]
    db.commit()
    return jobs


def _pending_item_review(db: Session, run_id: int) -> ReviewTask | None:
    return db.scalar(
        select(ReviewTask)
        .where(
            ReviewTask.processing_run_id == run_id,
            ReviewTask.status == "pending",
        )
        .order_by(ReviewTask.id.desc())
        .limit(1)
    )


def _pending_event_review(db: Session, run_id: int) -> EventReviewTask | None:
    return db.scalar(
        select(EventReviewTask)
        .where(
            EventReviewTask.event_aggregation_run_id == run_id,
            EventReviewTask.status == "pending",
        )
        .order_by(EventReviewTask.id.desc())
        .limit(1)
    )


def _active_item_run(db: Session, raw_item_id: int) -> ProcessingRun | None:
    return db.scalar(
        select(ProcessingRun)
        .where(
            ProcessingRun.raw_item_id == raw_item_id,
            ProcessingRun.status.in_(["running", "awaiting_review"]),
        )
        .order_by(ProcessingRun.id.desc())
        .limit(1)
    )


def _active_event_run(db: Session, item_id: int) -> EventAggregationRun | None:
    return db.scalar(
        select(EventAggregationRun)
        .where(
            EventAggregationRun.normalized_item_id == item_id,
            EventAggregationRun.status.in_(["running", "awaiting_review"]),
        )
        .order_by(EventAggregationRun.id.desc())
        .limit(1)
    )


async def execute_pipeline_job(db: Session, job: PipelineJob) -> None:
    raw_item = db.get(RawItem, job.raw_item_id)
    if raw_item is None:
        raise ValueError(f"raw item {job.raw_item_id} no longer exists")

    item_run = _active_item_run(db, raw_item.id)
    if item_run is None and (
        raw_item.normalized_item is None
        or raw_item.normalized_item.publication_status == "withdrawn"
    ):
        item_run = await start_item_processing(
            db,
            raw_item,
            execution_mode="automatic",
            correction_id=job.correction_id,
        )
    if item_run is not None:
        job.processing_run_id = item_run.id
        while item_run.status == "awaiting_review":
            review = _pending_item_review(db, item_run.id)
            if review is None:
                raise RuntimeError("automatic item run has no pending review")
            job.current_stage = review.stage
            review.decision_source = "automatic"
            review.policy_version = "auto-approve-v1"
            db.commit()
            item_run = await approve_review(
                db,
                review,
                note="automatic pipeline approval",
            )
        if item_run.status != "completed":
            raise RuntimeError(
                f"automatic item run stopped with status={item_run.status}"
            )
        if item_run.outcome == "irrelevant":
            return

    db.refresh(raw_item)
    item = raw_item.normalized_item
    if item is None or item.publication_status != "published":
        raise RuntimeError("automatic item pipeline did not publish a normalized item")

    event_run = _active_event_run(db, item.id)
    if event_run is None:
        job.current_stage = "event_decision"
        db.commit()
        event_run = await start_event_aggregation(
            db,
            item,
            execution_mode="automatic",
            correction_id=job.correction_id,
        )
    job.event_aggregation_run_id = event_run.id
    if event_run.status == "awaiting_review":
        review = _pending_event_review(db, event_run.id)
        if review is None:
            raise RuntimeError("automatic event run has no pending review")
        job.current_stage = "event_decision"
        review.decision_source = "automatic"
        review.policy_version = "auto-approve-v1"
        db.commit()
        event_run = approve_event_review(
            db,
            review,
            note="automatic pipeline approval",
        )
    if event_run.status != "completed":
        raise RuntimeError(
            f"automatic event run stopped with status={event_run.status}"
        )


def _claim_next_job(db: Session) -> PipelineJob | None:
    job = db.scalar(
        select(PipelineJob)
        .where(PipelineJob.status == "queued")
        .order_by(PipelineJob.created_at, PipelineJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.status = "running"
    job.attempts += 1
    job.started_at = datetime.now(UTC)
    job.error_message = None
    db.commit()
    db.refresh(job)
    return job


def _latest_checkpoint(db: Session, raw_item_id: int) -> ProcessingCheckpoint | None:
    return db.scalar(
        select(ProcessingCheckpoint)
        .where(
            ProcessingCheckpoint.raw_item_id == raw_item_id,
            ProcessingCheckpoint.invalidated_at.is_(None),
        )
        .order_by(ProcessingCheckpoint.id.desc())
        .limit(1)
    )


async def process_next_job() -> bool:
    with SessionLocal() as db:
        job = _claim_next_job(db)
        if job is None:
            return False
        try:
            await execute_pipeline_job(db, job)
            job = db.get(PipelineJob, job.id)
            job.status = "completed"
            job.completed_at = datetime.now(UTC)
            checkpoint = _latest_checkpoint(db, job.raw_item_id)
            job.last_checkpoint_id = checkpoint.id if checkpoint else None
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(PipelineJob, job.id)
            job.status = "failed"
            job.error_message = str(exc)[:4000]
            job.completed_at = datetime.now(UTC)
            checkpoint = _latest_checkpoint(db, job.raw_item_id)
            job.last_checkpoint_id = checkpoint.id if checkpoint else None
            if job.correction_id:
                correction = db.get(PipelineCorrection, job.correction_id)
                if correction is not None:
                    correction.status = "failed"
                    correction.error_message = job.error_message
            db.commit()
        return True


async def worker_loop() -> None:
    while True:
        processed = await process_next_job()
        if not processed:
            await asyncio.sleep(settings.pipeline_worker_poll_seconds)
