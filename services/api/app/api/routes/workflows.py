from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.domain.message_taxonomy import classification_error, content_analysis_error
from app.models.media_extraction import MediaExtraction
from app.models.pipeline import ProcessingCheckpoint
from app.models.workflow import ProcessingRun, ReviewTask
from app.schemas.workflow import (
    OCRWorkflowReviewRead,
    ProcessingRunRead,
    OCRReviewCorrection,
    ReviewQueueItemRead,
    ReviewApproval,
    ReviewCorrectionApproval,
    ReviewRejection,
    ReviewTaskRead,
)
from app.services.llm import LLMAnalysisError, LLMConfigurationError
from app.services.media_ocr import OCRProcessingError
from app.workflows.reviewed_pipeline import (
    approve_review,
    correct_ocr_review,
    reject_review,
    retry_processing_run,
)

router = APIRouter()


def _corrected_review_proposal(
    review: ReviewTask,
    payload: ReviewCorrectionApproval,
) -> dict[str, object]:
    corrections = payload.model_dump(exclude={"note"}, exclude_none=True)
    allowed_fields = {
        "relevance": {"decision"},
        "message_analysis": {"products", "content_form"},
        "importance": {"message_type", "topics", "importance_score"},
    }.get(review.stage, set())
    if unsupported := set(corrections).difference(allowed_fields):
        raise ValueError(f"stage={review.stage} 不支持修正字段: {', '.join(sorted(unsupported))}")
    proposal = {**review.proposal, **corrections}
    if review.stage == "message_analysis":
        error = content_analysis_error(
            products=list(proposal.get("products") or []),
            content_form=str(proposal.get("content_form") or ""),
        )
        if error:
            raise ValueError(error)
    if review.stage == "importance" and (
        payload.message_type is not None or payload.topics is not None
    ):
        analysis = review.processing_run.context.get("approved_message_analysis_proposal")
        if not isinstance(analysis, dict):
            raise ValueError("importance 修正缺少已批准的消息内容分析")
        classification_source = dict(analysis.get("classification_source") or {})
        source_kind = str(classification_source.get("source_kind") or "")
        if not source_kind:
            source_kind = (
                "unknown"
                if str(analysis.get("content_form") or "") == "repost"
                else "official"
                if review.processing_run.raw_item.source.is_official
                else "unofficial"
            )
        if source_kind not in {"official", "unofficial", "unknown"}:
            source_kind = "unknown"
        error = classification_error(
            products=list(analysis.get("products") or []),
            content_form=str(analysis.get("content_form") or ""),
            message_type=str(proposal.get("message_type") or ""),
            topics=list(proposal.get("topics") or []),
            source_kind=source_kind,
        )
        if error:
            raise ValueError(error)
    if review.stage != "importance" or payload.importance_score is None:
        return proposal

    calculation = dict(review.proposal.get("importance_calculation") or {})
    computed_score = calculation.get(
        "computed_score",
        review.proposal.get("importance_score"),
    )
    calculation.update(
        {
            "computed_score": computed_score,
            "manual_override": {
                "score": payload.importance_score,
                "reason": payload.note or "管理台修正后批准",
            },
            "final_score": payload.importance_score,
        }
    )
    proposal["importance_calculation"] = calculation
    return proposal


def _ocr_review_payload(
    db: Session,
    review: ReviewTask,
) -> dict[str, object]:
    run = review.processing_run
    raw_item = run.raw_item
    extraction_ids = [
        value
        for value in review.proposal.get("approved_media_extraction_ids", [])
        if isinstance(value, int)
    ]
    extractions = [
        extraction
        for extraction_id in extraction_ids
        if (extraction := db.get(MediaExtraction, extraction_id)) is not None
        and extraction.media_asset.raw_item_id == raw_item.id
    ]
    return {
        "review_id": review.id,
        "processing_run_id": run.id,
        "raw_item_id": raw_item.id,
        "raw_title": raw_item.display_title,
        "canonical_url": raw_item.canonical_url,
        "status": review.status,
        "corrections": list(review.proposal.get("ocr_corrections", [])),
        "extractions": [
            {
                "id": extraction.id,
                "media_asset_id": extraction.media_asset_id,
                "block_index": extraction.media_asset.block_index,
                "source_url": extraction.media_asset.source_url,
                "storage_path": extraction.media_asset.storage_path,
                "confidence": extraction.confidence,
                "raw_ocr_text": extraction.raw_ocr_text,
                "table_data": dict(extraction.processing_config.get("table_data", {})),
            }
            for extraction in extractions
        ],
        "created_at": review.created_at,
    }


@router.get("/runs", response_model=list[ProcessingRunRead])
def list_processing_runs(
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
) -> list[ProcessingRun]:
    statement = select(ProcessingRun).order_by(ProcessingRun.created_at.desc()).limit(200)
    if status_filter:
        statement = statement.where(ProcessingRun.status == status_filter)
    return list(db.scalars(statement))


@router.get("/runs/{run_id}", response_model=ProcessingRunRead)
def get_processing_run(run_id: int, db: Session = Depends(get_db)) -> ProcessingRun:
    run = db.get(ProcessingRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="processing run not found")
    return run


@router.get("/runs/{run_id}/reviews", response_model=list[ReviewTaskRead])
def list_run_reviews(run_id: int, db: Session = Depends(get_db)) -> list[ReviewTask]:
    if not db.get(ProcessingRun, run_id):
        raise HTTPException(status_code=404, detail="processing run not found")
    return list(
        db.scalars(
            select(ReviewTask)
            .where(ReviewTask.processing_run_id == run_id)
            .order_by(ReviewTask.created_at.asc())
        )
    )


@router.get("/reviews", response_model=list[ReviewTaskRead])
def list_reviews(
    status_filter: str | None = Query(default="pending", alias="status"),
    stage: str | None = None,
    db: Session = Depends(get_db),
) -> list[ReviewTask]:
    statement = select(ReviewTask).order_by(ReviewTask.created_at.desc()).limit(200)
    if status_filter:
        statement = statement.where(ReviewTask.status == status_filter)
    if stage:
        statement = statement.where(ReviewTask.stage == stage)
    return list(db.scalars(statement))


@router.get("/ocr-reviews", response_model=list[OCRWorkflowReviewRead])
def list_ocr_reviews(
    status_filter: str | None = Query(default="pending", alias="status"),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    statement = (
        select(ReviewTask)
        .where(ReviewTask.stage == "image_ocr")
        .order_by(ReviewTask.created_at.desc())
        .limit(200)
    )
    if status_filter:
        statement = statement.where(ReviewTask.status == status_filter)

    return [_ocr_review_payload(db, review) for review in db.scalars(statement)]


@router.get("/review-queue", response_model=list[ReviewQueueItemRead])
def list_review_queue(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    message_reviews = list(
        db.scalars(
            select(ReviewTask)
            .options(selectinload(ReviewTask.processing_run).selectinload(ProcessingRun.raw_item))
            .where(
                ReviewTask.status == "pending",
            )
            .order_by(ReviewTask.created_at.desc())
            .limit(200)
        )
    )
    raw_item_ids = {review.processing_run.raw_item_id for review in message_reviews}
    completed_by_raw: dict[int, list[str]] = {raw_item_id: [] for raw_item_id in raw_item_ids}
    if raw_item_ids:
        checkpoints = db.scalars(
            select(ProcessingCheckpoint)
            .where(
                ProcessingCheckpoint.raw_item_id.in_(raw_item_ids),
                ProcessingCheckpoint.invalidated_at.is_(None),
            )
            .order_by(ProcessingCheckpoint.id)
        )
        for checkpoint in checkpoints:
            stages = completed_by_raw[checkpoint.raw_item_id]
            if checkpoint.stage not in stages:
                stages.append(checkpoint.stage)

    payloads: list[dict[str, object]] = []
    for review in message_reviews:
        run = review.processing_run
        raw_item = run.raw_item
        is_ocr = review.stage == "image_ocr"
        payloads.append(
            {
                "raw_item_id": raw_item.id,
                "raw_title": raw_item.display_title,
                "canonical_url": raw_item.canonical_url,
                "source_name": raw_item.source.name,
                "processing_run_id": run.id,
                "normalized_item_id": (
                    raw_item.normalized_item.id if raw_item.normalized_item is not None else None
                ),
                "current_stage": review.stage,
                "completed_stages": completed_by_raw.get(raw_item.id, []),
                "review_kind": "ocr" if is_ocr else "message",
                "message_review": None if is_ocr else review,
                "ocr_review": (_ocr_review_payload(db, review) if is_ocr else None),
                "created_at": review.created_at,
            }
        )
    return sorted(
        payloads,
        key=lambda payload: payload["created_at"],
        reverse=True,
    )


@router.post("/reviews/{review_id}/approve", response_model=ProcessingRunRead)
async def approve_review_task(
    review_id: int,
    payload: ReviewApproval,
    db: Session = Depends(get_db),
) -> object:
    review = db.get(ReviewTask, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="review task not found")
    try:
        return await approve_review(db, review, note=payload.note)
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except (LLMAnalysisError, OCRProcessingError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/reviews/{review_id}/reject", response_model=ProcessingRunRead)
def reject_review_task(
    review_id: int,
    payload: ReviewRejection,
    db: Session = Depends(get_db),
) -> ProcessingRun:
    review = db.get(ReviewTask, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="review task not found")
    try:
        return reject_review(db, review, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/reviews/{review_id}/correct-and-approve",
    response_model=ProcessingRunRead,
)
async def correct_and_approve_review_task(
    review_id: int,
    payload: ReviewCorrectionApproval,
    db: Session = Depends(get_db),
) -> object:
    review = db.get(ReviewTask, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="review task not found")
    review.proposal = _corrected_review_proposal(review, payload)
    try:
        return await approve_review(
            db,
            review,
            note=payload.note or "管理台修正后批准",
        )
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except (LLMAnalysisError, OCRProcessingError, RuntimeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/reviews/{review_id}/correct-ocr", response_model=ProcessingRunRead)
async def correct_review_ocr(
    review_id: int,
    payload: OCRReviewCorrection,
    db: Session = Depends(get_db),
) -> object:
    review = db.get(ReviewTask, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="review task not found")
    try:
        return await correct_ocr_review(db, review, payload=payload)
    except LLMConfigurationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except (LLMAnalysisError, RuntimeError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/retry", response_model=ProcessingRunRead)
async def retry_run(run_id: int, db: Session = Depends(get_db)) -> object:
    run = db.get(ProcessingRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="processing run not found")
    try:
        return await retry_processing_run(db, run)
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except (LLMAnalysisError, OCRProcessingError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
