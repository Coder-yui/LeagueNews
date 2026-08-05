from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event import EventAggregationRun, EventMessage, EventRevision
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineCorrection, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.workflow import ProcessingRun
from app.schemas.pipeline import PipelineCorrectionCreate
from app.services.claims import (
    unlink_item_claims_from_event,
    withdraw_active_claims,
)
from app.services.event_aggregation import refresh_event_projection
from app.services.automatic_pipeline import enqueue_pipeline_job
from app.services.media_publication import withdraw_raw_item_media
from app.workflows.event_aggregation import start_event_aggregation
from app.workflows.reviewed_pipeline import (
    CLASSIFY_STAGE,
    FACT_STAGE,
    ITEM_STAGE,
    OCR_STAGE,
    RELEVANCE_STAGE,
    TRANSLATION_STAGE,
    start_item_processing,
)
from app.workflows.understand_media import is_patch_preview

EVENT_STAGE = "event_decision"


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


def _latest_event_run(db: Session, item_id: int) -> EventAggregationRun | None:
    return db.scalar(
        select(EventAggregationRun)
        .where(EventAggregationRun.normalized_item_id == item_id)
        .order_by(EventAggregationRun.id.desc())
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
        FACT_STAGE: TRANSLATION_STAGE,
        CLASSIFY_STAGE: FACT_STAGE,
        ITEM_STAGE: CLASSIFY_STAGE,
        EVENT_STAGE: ITEM_STAGE,
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
    db: Session,
    *,
    source_run: ProcessingRun | None,
    checkpoint: ProcessingCheckpoint | None,
    restart_from_stage: str,
) -> dict[str, Any]:
    if restart_from_stage in {RELEVANCE_STAGE, OCR_STAGE}:
        return {}
    context = dict(source_run.context) if source_run is not None else {}
    if restart_from_stage == TRANSLATION_STAGE:
        extraction_ids = context.get("approved_media_extraction_ids")
        if extraction_ids is None and checkpoint is not None:
            extraction_ids = checkpoint.artifact_references.get(
                "approved_media_extraction_ids", []
            )
        return {"approved_media_extraction_ids": extraction_ids or []}
    if restart_from_stage in {FACT_STAGE, CLASSIFY_STAGE, ITEM_STAGE}:
        translation = context.get("approved_translation_proposal")
        if (
            translation is None
            and restart_from_stage == FACT_STAGE
            and checkpoint is not None
        ):
            translation = checkpoint.output_snapshot
        if translation is None:
            raise ValueError(
                "no approved translation checkpoint is available; restart from translation"
            )
        result = {
            "approved_media_extraction_ids": context.get(
                "approved_media_extraction_ids", []
            ),
            "approved_translation_proposal": translation,
        }
        if restart_from_stage in {CLASSIFY_STAGE, ITEM_STAGE}:
            facts = context.get("approved_fact_proposal")
            if (
                facts is None
                and restart_from_stage == CLASSIFY_STAGE
                and checkpoint is not None
            ):
                facts = checkpoint.output_snapshot
            if facts is None:
                raise ValueError(
                    "no approved fact checkpoint is available; restart from fact_extract"
                )
            result["approved_fact_proposal"] = facts
        if restart_from_stage == ITEM_STAGE:
            classification = context.get("approved_classification_proposal")
            if classification is None and checkpoint is not None:
                classification = checkpoint.output_snapshot
            if classification is None:
                raise ValueError(
                    "no approved classification checkpoint is available; "
                    "restart from classify"
                )
            result["approved_classification_proposal"] = classification
        return result
    return {}


def _supersede_active_work(db: Session, *, raw_item_id: int, item_id: int) -> None:
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
    for run in db.scalars(
        select(EventAggregationRun).where(
            EventAggregationRun.normalized_item_id == item_id,
            EventAggregationRun.status.in_(["running", "awaiting_review"]),
        )
    ):
        run.status = "superseded"
        run.outcome = "correction_requested"
        run.completed_at = now
        for review in run.reviews:
            if review.status == "pending":
                review.status = "superseded"
                review.resolved_at = now


def _withdraw_event_membership(
    db: Session,
    *,
    item: NormalizedItem,
    correction: PipelineCorrection,
) -> int | None:
    memberships = list(
        db.scalars(
            select(EventMessage).where(
                EventMessage.normalized_item_id == item.id,
                EventMessage.membership_status == "active",
            )
        )
    )
    if not memberships:
        return None
    now = datetime.now(UTC)
    for membership in memberships:
        event = membership.event
        unlink_item_claims_from_event(
            db,
            normalized_item_id=item.id,
            event_id=event.id,
        )
        membership.membership_status = "withdrawn"
        membership.withdrawn_at = now
        membership.withdrawal_reason = correction.reason
        membership.source_correction_id = correction.id
        event.current_revision += 1
        db.flush()
        refresh_event_projection(db, event)
        db.add(
            EventRevision(
                event_id=event.id,
                revision=event.current_revision,
                title=event.title,
                summary=event.summary,
                change_note=f"撤回消息 {item.id}：{correction.reason}",
                evidence_snapshot={
                    "action": "withdraw_membership",
                    "normalized_item_id": item.id,
                    "correction_id": correction.id,
                },
            )
        )
    return memberships[0].event_id


async def create_and_start_correction(
    db: Session,
    *,
    item: NormalizedItem,
    payload: PipelineCorrectionCreate,
    allow_withdrawn: bool = False,
) -> PipelineCorrection:
    if item.publication_status != "published" and not allow_withdrawn:
        raise ValueError("normalized item is already withdrawn")
    if item.publication_status != "published" and payload.restart_from_stage == EVENT_STAGE:
        raise ValueError("event_decision recovery requires a published normalized item")
    source_run = _latest_processing_run(db, item.raw_item_id)
    source_event_run = _latest_event_run(db, item.id)
    checkpoint = _checkpoint_before(
        db,
        raw_item_id=item.raw_item_id,
        restart_from_stage=payload.restart_from_stage,
    )
    if payload.restart_from_stage == OCR_STAGE and not is_patch_preview(item.raw_item):
        raise ValueError(
            "image_ocr is not applicable to this raw item; restart from translation"
        )
    resume_context = (
        _resume_context(
            db,
            source_run=source_run,
            checkpoint=checkpoint,
            restart_from_stage=payload.restart_from_stage,
        )
        if payload.restart_from_stage != EVENT_STAGE
        else {}
    )
    correction = PipelineCorrection(
        raw_item_id=item.raw_item_id,
        normalized_item_id=item.id,
        source_processing_run_id=source_run.id if source_run else None,
        source_event_run_id=source_event_run.id if source_event_run else None,
        checkpoint_id=checkpoint.id if checkpoint else None,
        restart_from_stage=payload.restart_from_stage,
        resume_mode=payload.resume_mode,
        reason=payload.reason,
        status="requested",
    )
    db.add(correction)
    db.flush()
    _supersede_active_work(db, raw_item_id=item.raw_item_id, item_id=item.id)
    correction.event_id = _withdraw_event_membership(
        db, item=item, correction=correction
    )
    if payload.restart_from_stage != EVENT_STAGE:
        item.publication_status = "withdrawn"
        item.withdrawn_at = datetime.now(UTC)
        item.withdrawal_reason = payload.reason
        withdraw_active_claims(db, normalized_item_id=item.id)
        withdraw_raw_item_media(item.raw_item)
    correction.status = "running"
    correction.started_at = datetime.now(UTC)
    db.commit()
    db.refresh(correction)

    try:
        if payload.restart_from_stage == EVENT_STAGE:
            await start_event_aggregation(
                db,
                item,
                supersedes_run_id=source_event_run.id if source_event_run else None,
                execution_mode=payload.resume_mode,
                correction_id=correction.id,
            )
        else:
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
    if payload.restart_from_stage == EVENT_STAGE:
        raise ValueError("event_decision recovery requires a published normalized item")

    source_run = _latest_processing_run(db, raw_item.id)
    checkpoint = _checkpoint_before(
        db,
        raw_item_id=raw_item.id,
        restart_from_stage=payload.restart_from_stage,
    )
    if payload.restart_from_stage == OCR_STAGE and not is_patch_preview(raw_item):
        raise ValueError(
            "image_ocr is not applicable to this raw item; restart from translation"
        )
    context = _resume_context(
        db,
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
