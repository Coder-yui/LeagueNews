from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.workflow import ProcessingRun, ReviewTask
from app.schemas.workflow import (
    ProcessingRunRead,
    OCRReviewCorrection,
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
    else:
        statement = statement.where(ReviewTask.stage != "relevance")
    return list(db.scalars(statement))


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
    corrections = payload.model_dump(exclude={"note"}, exclude_none=True)
    review.proposal = {**review.proposal, **corrections}
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
