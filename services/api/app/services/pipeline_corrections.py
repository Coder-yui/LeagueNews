from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineCorrection, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.workflow import ProcessingRun
from app.schemas.pipeline import PipelineCorrectionCreate
from app.services.automatic_pipeline import enqueue_pipeline_job
from app.services.media_publication import withdraw_raw_item_media
from app.workflows.reviewed_pipeline import (
    IMPORTANCE_STAGE,
    MESSAGE_ANALYSIS_STAGE,
    OCR_STAGE,
    RELEVANCE_STAGE,
    TRANSLATION_STAGE,
    start_item_processing,
)
from app.workflows.understand_media import is_patch_preview


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


def _resume_context(
    *,
    source_run: ProcessingRun | None,
    checkpoint: ProcessingCheckpoint | None,
    restart_from_stage: str,
) -> dict[str, Any]:
    if restart_from_stage == RELEVANCE_STAGE:
        return {}
    context = dict(source_run.context) if source_run is not None else {}
    relevance_context = {
        key: context[key] for key in ("evidence_gate", "relevance_decision") if key in context
    }
    if restart_from_stage == OCR_STAGE:
        return relevance_context
    if restart_from_stage == TRANSLATION_STAGE:
        extraction_ids = context.get("approved_media_extraction_ids")
        if extraction_ids is None and checkpoint is not None:
            extraction_ids = checkpoint.artifact_references.get("approved_media_extraction_ids", [])
        return {
            **relevance_context,
            "approved_media_extraction_ids": extraction_ids or [],
        }
    if restart_from_stage in {MESSAGE_ANALYSIS_STAGE, IMPORTANCE_STAGE}:
        translation = context.get("approved_translation_proposal")
        if (
            translation is None
            and restart_from_stage == MESSAGE_ANALYSIS_STAGE
            and checkpoint is not None
        ):
            translation = checkpoint.output_snapshot
        if translation is None:
            raise ValueError(
                "no approved translation checkpoint is available; restart from translation"
            )
        result = {
            **relevance_context,
            "approved_media_extraction_ids": context.get("approved_media_extraction_ids", []),
            "approved_translation_proposal": translation,
        }
        if restart_from_stage == IMPORTANCE_STAGE:
            analysis = context.get("approved_message_analysis_proposal")
            if analysis is None and checkpoint is not None:
                analysis = checkpoint.output_snapshot
            if analysis is None:
                raise ValueError(
                    "no approved message analysis checkpoint is available; "
                    "restart from message_analysis"
                )
            result["approved_message_analysis_proposal"] = analysis
        return result
    return {}


def _supersede_active_work(db: Session, *, raw_item_id: int) -> None:
    now = datetime.now(UTC)
    for run in db.scalars(
        select(ProcessingRun).where(
            ProcessingRun.raw_item_id == raw_item_id,
            ProcessingRun.status.in_(["running", "awaiting_review"]),
        )
    ):
        run.status = "superseded"
        run.outcome = "correction_requested"
        run.completed_at = now
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
    resume_context = _resume_context(
        source_run=source_run,
        checkpoint=checkpoint,
        restart_from_stage=payload.restart_from_stage,
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
            restart_from_stage=payload.restart_from_stage,
            context=resume_context,
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
) -> PipelineCorrection:
    from app.models.pipeline import PipelineJob

    job = db.get(PipelineJob, job_id)
    if job is None:
        raise LookupError("pipeline job not found")
    if job.status != "failed":
        raise ValueError(f"pipeline job cannot recover from status={job.status}")
    raw_item = db.get(RawItem, job.raw_item_id)
    if raw_item is None:
        raise ValueError("raw item no longer exists")
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
    context = _resume_context(
        source_run=source_run,
        checkpoint=checkpoint,
        restart_from_stage=payload.restart_from_stage,
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
            restart_from_stage=payload.restart_from_stage,
            context=context,
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
