from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.event import EventAggregationRun, EventReviewTask
from app.models.normalized_item import NormalizedItem
from app.schemas.event_workflow import (
    EventAggregationRunRead,
    EventReviewApproval,
    EventReviewCorrectionApproval,
    EventReviewRejection,
    EventReviewTaskRead,
)
from app.services.llm import LLMAnalysisError, LLMConfigurationError
from app.workflows.event_aggregation import (
    approve_event_review,
    reject_event_review,
    retry_event_aggregation,
    start_event_aggregation,
    validate_event_decision,
)

router = APIRouter()


@router.post("/items/{item_id}/process", response_model=EventAggregationRunRead)
async def process_item(item_id: int, db: Session = Depends(get_db)) -> object:
    item = db.get(NormalizedItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="normalized item not found")
    try:
        return await start_event_aggregation(db, item)
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMAnalysisError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs", response_model=list[EventAggregationRunRead])
def list_runs(
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
) -> list[EventAggregationRun]:
    statement = select(EventAggregationRun).order_by(
        EventAggregationRun.created_at.desc()
    ).limit(200)
    if status_filter:
        statement = statement.where(EventAggregationRun.status == status_filter)
    return list(db.scalars(statement))


@router.get("/reviews", response_model=list[EventReviewTaskRead])
def list_reviews(
    status_filter: str | None = Query(default="pending", alias="status"),
    db: Session = Depends(get_db),
) -> list[EventReviewTask]:
    statement = select(EventReviewTask).order_by(
        EventReviewTask.created_at.desc()
    ).limit(200)
    if status_filter:
        statement = statement.where(EventReviewTask.status == status_filter)
    return list(db.scalars(statement))


@router.post(
    "/reviews/{review_id}/approve",
    response_model=EventAggregationRunRead,
)
def approve_review(
    review_id: int,
    payload: EventReviewApproval,
    db: Session = Depends(get_db),
) -> EventAggregationRun:
    review = db.get(EventReviewTask, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="event review not found")
    try:
        return approve_event_review(db, review, note=payload.note)
    except (RuntimeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/reviews/{review_id}/reject",
    response_model=EventAggregationRunRead,
)
def reject_review(
    review_id: int,
    payload: EventReviewRejection,
    db: Session = Depends(get_db),
) -> EventAggregationRun:
    review = db.get(EventReviewTask, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="event review not found")
    try:
        return reject_event_review(db, review, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/reviews/{review_id}/correct-and-approve",
    response_model=EventAggregationRunRead,
)
def correct_and_approve_event_review(
    review_id: int,
    payload: EventReviewCorrectionApproval,
    db: Session = Depends(get_db),
) -> EventAggregationRun:
    review = db.get(EventReviewTask, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="event review not found")
    try:
        decision = validate_event_decision(review.run, payload.decision_draft)
        corrected = {
            **decision.model_dump(mode="json"),
            "_execution_metadata": review.run.decision_draft.get("_execution_metadata", {}),
            "knowledge_rule_ids": review.run.decision_draft.get("knowledge_rule_ids", []),
        }
        review.run.decision_draft = corrected
        review.proposal = {
            **dict(review.proposal),
            "decision": corrected,
        }
        return approve_event_review(
            db,
            review,
            note=payload.note or "管理台修改归属后批准",
        )
    except (RuntimeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/retry", response_model=EventAggregationRunRead)
async def retry_run(run_id: int, db: Session = Depends(get_db)) -> object:
    run = db.get(EventAggregationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="event run not found")
    try:
        return await retry_event_aggregation(db, run)
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except LLMAnalysisError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
