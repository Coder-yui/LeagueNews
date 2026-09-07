"""Review edits and feedback persistence shared by the V3 API."""

from datetime import UTC, datetime
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.workflow import ProcessingRun, ReviewTask, KnowledgeRule, GlossaryTerm
from app.models.pipeline import PipelineCorrection
from app.models.media_extraction import MediaExtraction
from app.schemas.workflow import ReviewRejection, OCRReviewCorrection

RELEVANCE_STAGE = "relevance"
OCR_STAGE = "image_ocr"
TRANSLATION_STAGE = "translation"
MESSAGE_ANALYSIS_STAGE = "message_analysis"
IMPORTANCE_STAGE = "importance"


def reject_review(
    db: Session,
    review: ReviewTask,
    *,
    payload: ReviewRejection,
) -> ProcessingRun:
    locked_review = db.scalar(
        select(ReviewTask)
        .where(ReviewTask.id == review.id)
        .with_for_update()
    )
    if locked_review is None:
        raise ValueError("review task not found")
    if locked_review.status == "rejected" and locked_review.delivery_status in {
        "recorded",
        "consumed",
    }:
        run = db.get(ProcessingRun, locked_review.processing_run_id)
        if run is None:
            raise ValueError("processing run not found")
        return run
    _require_pending_review(locked_review)
    _validate_rejection(locked_review.stage, payload)
    review = locked_review
    now = datetime.now(UTC)
    review.status = "rejected"
    review.feedback = payload.model_dump(mode="json")
    review.resolved_at = now
    review.delivery_status = "recorded"
    run = review.processing_run
    run.status = "rejected"
    run.outcome = "review_rejected"
    run.current_stage = review.stage
    run.completed_at = now
    _set_correction_terminal(db, run, status="cancelled", completed_at=now)

    if payload.feedback_type == "analysis_correction" and review.stage != RELEVANCE_STAGE:
        db.add(
            KnowledgeRule(
                knowledge_type="analysis",
                scope=payload.knowledge_scope,
                rule_text=payload.knowledge_rule or payload.reason or "",
                correction_data=payload.corrected_values,
                source_review_id=review.id,
                lifecycle_status="draft",
            )
        )
    elif payload.feedback_type in {"translation_term", "translation_correction"}:
        if payload.reason:
            db.add(
                KnowledgeRule(
                    knowledge_type="translation",
                    scope=payload.knowledge_scope,
                    rule_text=payload.reason,
                    correction_data=payload.corrected_values,
                    source_review_id=review.id,
                    lifecycle_status="draft",
                )
            )
        for correction in payload.glossary_updates:
            db.add(
                GlossaryTerm(
                    source_term=correction.source_term,
                    preferred_translation=correction.preferred_translation,
                    forbidden_translations=correction.forbidden_translations,
                    scope=correction.scope,
                    notes=correction.notes or payload.reason,
                    source_review_id=review.id,
                    is_active=True,
                )
            )
    db.commit()
    db.refresh(run)
    return run


async def correct_ocr_review(
    db: Session,
    review: ReviewTask,
    *,
    payload: OCRReviewCorrection,
) -> ProcessingRun:
    _require_pending_review(review)
    if review.stage not in {OCR_STAGE, "media"}:
        raise ValueError("OCR correction is only available during image OCR review")
    run = review.processing_run
    approved_ids = _extraction_ids(review.proposal)
    if payload.extraction_id not in approved_ids:
        raise ValueError("media extraction is not part of this review proposal")
    original = db.get(MediaExtraction, payload.extraction_id)
    if not original or original.media_asset.raw_item_id != run.raw_item_id:
        raise ValueError("media extraction does not belong to this raw item")

    corrected_table = payload.table_data.model_dump(mode="json")
    processing_config = dict(original.processing_config)
    processing_config.update(
        {
            "table_data": corrected_table,
            "structure_confidence": 1.0,
            "manual_correction": {
                "corrected_from_extraction_id": original.id,
                "source_review_id": review.id,
                "note": payload.note,
                "corrected_at": datetime.now(UTC).isoformat(),
            },
        }
    )
    corrected = MediaExtraction(
        processing_run_id=run.id,
        artifact_scope=original.artifact_scope,
        media_asset_id=original.media_asset_id,
        task_type=original.task_type,
        provider="manual-table-correction",
        ocr_engine=original.ocr_engine,
        structuring_model="",
        schema_version="v2-ocr-review-manual",
        status="processed",
        raw_ocr_text=original.raw_ocr_text,
        ocr_lines=original.ocr_lines,
        structured_data={},
        processing_config=processing_config,
        confidence=original.confidence,
    )
    db.add(corrected)
    db.flush()

    replacement_ids = [
        corrected.id if extraction_id == original.id else extraction_id
        for extraction_id in approved_ids
    ]
    correction_history = list(review.proposal.get("ocr_corrections", []))
    correction_history.append(
        {
            "corrected_from_extraction_id": original.id,
            "corrected_extraction_id": corrected.id,
            "note": payload.note,
        }
    )
    _replace_pending_review(
        db,
        run=run,
        stage=review.stage,
        proposal={
            **review.proposal,
            "extraction_ids": replacement_ids,
            "approved_media_extraction_ids": replacement_ids,
            "ocr_corrections": correction_history,
        },
    )
    db.commit()
    db.refresh(run)
    return run


def _replace_pending_review(
    db: Session,
    *,
    run: ProcessingRun,
    stage: str,
    proposal: dict[str, Any],
) -> ReviewTask:
    for review in run.reviews:
        if review.status == "pending":
            review.status = "superseded"
            review.resolved_at = datetime.now(UTC)
    review = ReviewTask(
        processing_run_id=run.id,
        stage=stage,
        status="pending",
        proposal=proposal,
    )
    db.add(review)
    run.status = "awaiting_review"
    run.current_stage = stage
    run.error_message = None
    return review


def _validate_rejection(stage: str, payload: ReviewRejection) -> None:
    allowed = {
        RELEVANCE_STAGE: {"analysis_correction"},
        OCR_STAGE: {"ocr_error"},
        "media": {"ocr_error"},
        MESSAGE_ANALYSIS_STAGE: {"analysis_correction"},
        IMPORTANCE_STAGE: {"analysis_correction"},
        TRANSLATION_STAGE: {"translation_term", "translation_correction"},
    }
    if payload.feedback_type not in allowed.get(stage, set()):
        raise ValueError(f"feedback_type={payload.feedback_type} is invalid for stage={stage}")
    if (
        payload.feedback_type in {"translation_term", "translation_correction"}
        and not payload.reason
        and not payload.glossary_updates
    ):
        raise ValueError("translation rejection requires a reason or glossary update")


def _extraction_ids(payload: dict[str, Any]) -> list[int]:
    return [
        int(value)
        for value in payload.get("approved_media_extraction_ids", [])
        if isinstance(value, int)
    ]


def _require_pending_review(review: ReviewTask) -> None:
    if review.status != "pending":
        raise ValueError(f"review task cannot be resolved from status={review.status}")


def _set_correction_terminal(
    db: Session,
    run: ProcessingRun,
    *,
    status: str,
    completed_at: datetime,
    error_message: str | None = None,
) -> None:
    if not run.correction_id:
        return
    correction = db.get(PipelineCorrection, run.correction_id)
    if correction is None:
        return
    correction.status = status
    correction.completed_at = completed_at
    if error_message is not None:
        correction.error_message = error_message
