from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineCorrection, PipelineJob, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.workflow import ProcessingRun
from app.orchestration.contracts import ITEM_PROCESSING_GRAPH, ProcessingStage
from app.orchestration.item_processing.service import start_item_processing
from app.schemas.pipeline import PipelineCorrectionCreate
from app.services.media_publication import withdraw_raw_item_media
from app.services.pipeline_queue import enqueue_pipeline_job
from app.services.raw_item_versions import is_latest_raw_item
from app.services.review_actions import (
    IMPORTANCE_STAGE,
    MESSAGE_ANALYSIS_STAGE,
    OCR_STAGE,
    RELEVANCE_STAGE,
    TRANSLATION_STAGE,
)
from app.services.media_methods import is_patch_preview


def _latest_processing_run(db: Session, raw_item_id: int) -> ProcessingRun | None:
    return db.scalar(
        select(ProcessingRun)
        .where(
            ProcessingRun.raw_item_id == raw_item_id,
            ProcessingRun.workflow_type == "item",
        )
        .order_by(ProcessingRun.id.desc())
        .limit(1)
    )


def _graph_restart(
    source_run: ProcessingRun | None,
    requested_stage: str,
) -> tuple[ProcessingStage, int | None]:
    """Map the V2 correction vocabulary onto a V3 replay request.

    Legacy runs do not contain the V3 evidence checkpoint, so their first V3
    correction is intentionally rebuilt from immutable RawItem evidence.
    Subsequent V3 runs can replay any preserved prefix.
    """

    stage = {
        RELEVANCE_STAGE: ProcessingStage.RELEVANCE,
        OCR_STAGE: ProcessingStage.MEDIA,
        TRANSLATION_STAGE: ProcessingStage.TRANSLATION,
        MESSAGE_ANALYSIS_STAGE: ProcessingStage.MESSAGE_ANALYSIS,
        IMPORTANCE_STAGE: ProcessingStage.IMPORTANCE,
    }[requested_stage]
    if source_run is None or source_run.graph_name != ITEM_PROCESSING_GRAPH:
        return stage, None
    return stage, source_run.id


def _checkpoint_before(
    db: Session,
    *,
    raw_item_id: int,
    restart_from_stage: str,
) -> ProcessingCheckpoint | None:
    predecessor = {
        OCR_STAGE: RELEVANCE_STAGE,
        TRANSLATION_STAGE: OCR_STAGE,
        MESSAGE_ANALYSIS_STAGE: TRANSLATION_STAGE,
        IMPORTANCE_STAGE: MESSAGE_ANALYSIS_STAGE,
    }.get(restart_from_stage)
    if predecessor is None:
        return None
    return db.scalar(
        select(ProcessingCheckpoint)
        .where(
            ProcessingCheckpoint.raw_item_id == raw_item_id,
            ProcessingCheckpoint.stage == predecessor,
            ProcessingCheckpoint.invalidated_at.is_(None),
        )
        .order_by(ProcessingCheckpoint.id.desc())
        .limit(1)
    )


def _supersede_active_work(db: Session, *, raw_item_id: int) -> None:
    now = datetime.now(UTC)
    for job in db.scalars(
        select(PipelineJob).where(
            PipelineJob.raw_item_id == raw_item_id,
            or_(
                PipelineJob.status.in_(["queued", "running"]),
                and_(
                    PipelineJob.status == "failed",
                    PipelineJob.next_attempt_at.is_not(None),
                ),
            ),
            PipelineJob.job_type == "event",
        )
    ):
        job.status = "cancelled"
        job.error_message = "superseded by message correction"
        job.completed_at = now
        job.next_attempt_at = None
        job.worker_id = None
        job.lease_token = None
        job.lease_expires_at = None
        job.heartbeat_at = None
    for run in db.scalars(
        select(ProcessingRun).where(
            ProcessingRun.raw_item_id == raw_item_id,
            ProcessingRun.status.in_(["running", "awaiting_review"]),
        )
    ):
        run.status = "superseded"
        run.outcome = "correction_requested"
        run.completed_at = now
        if run.correction_id:
            previous_correction = db.get(PipelineCorrection, run.correction_id)
            if previous_correction is not None:
                previous_correction.status = "cancelled"
                previous_correction.completed_at = now
                previous_correction.error_message = "superseded by a newer correction"
        for review in run.reviews:
            if review.status == "pending":
                review.status = "superseded"
                review.resolved_at = now


async def create_and_start_correction(
    db: Session,
    *,
    item: NormalizedItem,
    payload: PipelineCorrectionCreate,
    allow_withdrawn: bool = False,
) -> PipelineCorrection:
    if item.publication_status != "published" and not allow_withdrawn:
        raise ValueError("normalized item is already withdrawn")
    source_run = _latest_processing_run(db, item.raw_item_id)
    checkpoint = _checkpoint_before(
        db,
        raw_item_id=item.raw_item_id,
        restart_from_stage=payload.restart_from_stage,
    )
    if payload.restart_from_stage == OCR_STAGE and not is_patch_preview(item.raw_item):
        raise ValueError("image_ocr is not applicable to this raw item; restart from translation")
    graph_stage, replay_from_run_id = _graph_restart(
        source_run, payload.restart_from_stage
    )
    correction = PipelineCorrection(
        raw_item_id=item.raw_item_id,
        normalized_item_id=item.id,
        source_processing_run_id=source_run.id if source_run else None,
        checkpoint_id=checkpoint.id if checkpoint else None,
        restart_from_stage=payload.restart_from_stage,
        resume_mode=payload.resume_mode,
        reason=payload.reason,
        status="requested",
    )
    db.add(correction)
    db.flush()
    _supersede_active_work(db, raw_item_id=item.raw_item_id)
    item.publication_status = "withdrawn"
    item.withdrawn_at = datetime.now(UTC)
    item.withdrawal_reason = payload.reason
    withdraw_raw_item_media(item.raw_item)
    correction.status = "running"
    correction.started_at = datetime.now(UTC)
    db.commit()
    db.refresh(correction)

    try:
        await start_item_processing(
            db,
            item.raw_item,
            supersedes_run_id=source_run.id if source_run else None,
            execution_mode=payload.resume_mode,
            correction_id=correction.id,
            restart_from_stage=graph_stage.value,
            replay_from_run_id=replay_from_run_id,
            allow_existing_projection=True,
            defer_execution=payload.resume_mode == "automatic",
        )
        if payload.resume_mode == "automatic":
            enqueue_pipeline_job(
                db,
                raw_item_id=item.raw_item_id,
                correction_id=correction.id,
                current_stage=payload.restart_from_stage,
            )
            db.commit()
    except Exception as exc:
        db.rollback()
        correction = db.get(PipelineCorrection, correction.id)
        correction.status = "failed"
        correction.error_message = str(exc)
        correction.completed_at = datetime.now(UTC)
        if payload.resume_mode == "automatic":
            job = enqueue_pipeline_job(
                db,
                raw_item_id=item.raw_item_id,
                correction_id=correction.id,
                current_stage=payload.restart_from_stage,
            )
            if job is not None:
                job.status = "failed"
                job.error_message = str(exc)[:4000]
                job.completed_at = datetime.now(UTC)
        db.commit()
        raise
    db.refresh(correction)
    return correction


async def recover_failed_job(
    db: Session,
    *,
    job_id: int,
    payload: PipelineCorrectionCreate,
) -> PipelineCorrection | PipelineJob:

    job = db.get(PipelineJob, job_id)
    if job is None:
        raise LookupError("pipeline job not found")
    if job.status != "failed":
        raise ValueError(f"pipeline job cannot recover from status={job.status}")
    if job.next_attempt_at is not None:
        raise ValueError("pipeline job is already scheduled for automatic retry")
    raw_item = db.get(RawItem, job.raw_item_id)
    if raw_item is None:
        raise ValueError("raw item no longer exists")
    if job.job_type == "event":
        item = raw_item.normalized_item
        if not is_latest_raw_item(db, raw_item) or item is None or item.publication_status != "published":
            job.status = "cancelled"
            job.error_message = "published NormalizedItem is no longer current"
            job.completed_at = datetime.now(UTC)
        elif db.scalar(
            select(PipelineJob).where(
                PipelineJob.raw_item_id == job.raw_item_id,
                PipelineJob.id != job.id,
                or_(
                    PipelineJob.status.in_(["queued", "running"]),
                    and_(
                        PipelineJob.status == "failed",
                        PipelineJob.next_attempt_at.is_not(None),
                    ),
                ),
            )
        ) is not None:
            job.status = "cancelled"
            job.error_message = "superseded by active pipeline job"
            job.completed_at = datetime.now(UTC)
        else:
            job.status = "queued"
            job.attempts = 0
            job.error_message = None
            job.completed_at = None
            job.worker_id = None
            job.lease_token = None
            job.lease_expires_at = None
            job.heartbeat_at = None
        db.commit()
        db.refresh(job)
        return job
    if raw_item.normalized_item is not None:
        return await create_and_start_correction(
            db,
            item=raw_item.normalized_item,
            payload=payload,
            allow_withdrawn=True,
        )
    source_run = _latest_processing_run(db, raw_item.id)
    checkpoint = _checkpoint_before(
        db,
        raw_item_id=raw_item.id,
        restart_from_stage=payload.restart_from_stage,
    )
    if payload.restart_from_stage == OCR_STAGE and not is_patch_preview(raw_item):
        raise ValueError("image_ocr is not applicable to this raw item; restart from translation")
    graph_stage, replay_from_run_id = _graph_restart(
        source_run, payload.restart_from_stage
    )
    correction = PipelineCorrection(
        raw_item_id=raw_item.id,
        source_processing_run_id=source_run.id if source_run else None,
        checkpoint_id=checkpoint.id if checkpoint else None,
        restart_from_stage=payload.restart_from_stage,
        resume_mode=payload.resume_mode,
        reason=payload.reason,
        status="running",
        started_at=datetime.now(UTC),
    )
    db.add(correction)
    db.commit()
    db.refresh(correction)
    try:
        await start_item_processing(
            db,
            raw_item,
            supersedes_run_id=source_run.id if source_run else None,
            execution_mode=payload.resume_mode,
            correction_id=correction.id,
            restart_from_stage=graph_stage.value,
            replay_from_run_id=replay_from_run_id,
            defer_execution=payload.resume_mode == "automatic",
        )
        if payload.resume_mode == "automatic":
            enqueue_pipeline_job(
                db,
                raw_item_id=raw_item.id,
                correction_id=correction.id,
                current_stage=payload.restart_from_stage,
            )
            db.commit()
    except Exception as exc:
        db.rollback()
        correction = db.get(PipelineCorrection, correction.id)
        correction.status = "failed"
        correction.error_message = str(exc)
        correction.completed_at = datetime.now(UTC)
        if payload.resume_mode == "automatic":
            failed_job = enqueue_pipeline_job(
                db,
                raw_item_id=raw_item.id,
                correction_id=correction.id,
                current_stage=payload.restart_from_stage,
            )
            if failed_job is not None:
                failed_job.status = "failed"
                failed_job.error_message = str(exc)[:4000]
                failed_job.completed_at = datetime.now(UTC)
        db.commit()
        raise
    db.refresh(correction)
    return correction


async def restart_raw_item_from_beginning(
    db: Session, *, raw_item_id: int
) -> PipelineCorrection | PipelineJob:
    """Restart a failed item with fresh relevance context in automatic mode."""
    raw_item = db.get(RawItem, raw_item_id)
    if raw_item is None:
        raise LookupError("raw item not found")
    if not is_latest_raw_item(db, raw_item):
        raise ValueError("raw item has been superseded by a newer revision")
    payload = PipelineCorrectionCreate(
        restart_from_stage=RELEVANCE_STAGE,
        resume_mode="automatic",
        reason="admin restart from beginning",
    )
    failed_job = db.scalar(
        select(PipelineJob)
        .where(PipelineJob.raw_item_id == raw_item.id, PipelineJob.status == "failed")
        .order_by(PipelineJob.id.desc())
        .limit(1)
    )
    if failed_job is not None:
        return await recover_failed_job(db, job_id=failed_job.id, payload=payload)

    source_run = _latest_processing_run(db, raw_item.id)
    if source_run is None or source_run.status != "failed":
        raise ValueError("raw item has no failed processing run to restart")
    active_job = db.scalar(
        select(PipelineJob).where(
            PipelineJob.raw_item_id == raw_item.id,
            PipelineJob.status.in_(["queued", "running", "paused"]),
        )
    )
    if active_job is not None:
        raise ValueError(f"raw item already has active pipeline job {active_job.id}")

    correction = PipelineCorrection(
        raw_item_id=raw_item.id,
        normalized_item_id=raw_item.normalized_item.id if raw_item.normalized_item else None,
        source_processing_run_id=source_run.id,
        restart_from_stage=RELEVANCE_STAGE,
        resume_mode="automatic",
        reason=payload.reason,
        status="running",
        started_at=datetime.now(UTC),
    )
    db.add(correction)
    db.commit()
    db.refresh(correction)
    try:
        graph_stage, replay_from_run_id = _graph_restart(
            source_run, RELEVANCE_STAGE
        )
        await start_item_processing(
            db,
            raw_item,
            supersedes_run_id=source_run.id,
            execution_mode="automatic",
            correction_id=correction.id,
            restart_from_stage=graph_stage.value,
            replay_from_run_id=replay_from_run_id,
            allow_existing_projection=raw_item.normalized_item is not None,
            defer_execution=True,
        )
        enqueue_pipeline_job(
            db,
            raw_item_id=raw_item.id,
            correction_id=correction.id,
            current_stage=RELEVANCE_STAGE,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        failed = db.get(PipelineCorrection, correction.id)
        if failed is not None:
            failed.status = "failed"
            failed.error_message = str(exc)
            failed.completed_at = datetime.now(UTC)
            db.commit()
        raise
    db.refresh(correction)
    return correction
