from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineCorrection, PipelineJob, ProcessingCheckpoint
from app.schemas.pipeline import (
    PipelineCorrectionCreate,
    PipelineCorrectionRead,
    PipelineJobRead,
    ProcessingCheckpointRead,
)
from app.services.pipeline_corrections import create_and_start_correction, recover_failed_job
from app.services.automatic_pipeline import enqueue_pending_raw_items

router = APIRouter()


@router.post(
    "/normalized-items/{item_id}/corrections",
    response_model=PipelineCorrectionRead,
)
async def correct_normalized_item(
    item_id: int,
    payload: PipelineCorrectionCreate,
    db: Session = Depends(get_db),
) -> PipelineCorrection:
    item = db.get(NormalizedItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="normalized item not found")
    try:
        return await create_and_start_correction(db, item=item, payload=payload)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/corrections", response_model=list[PipelineCorrectionRead])
def list_corrections(
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
) -> list[PipelineCorrection]:
    statement = select(PipelineCorrection).order_by(
        PipelineCorrection.requested_at.desc()
    ).limit(200)
    if status_filter:
        statement = statement.where(PipelineCorrection.status == status_filter)
    return list(db.scalars(statement))


@router.get("/checkpoints", response_model=list[ProcessingCheckpointRead])
def list_checkpoints(
    raw_item_id: int | None = None,
    db: Session = Depends(get_db),
) -> list[ProcessingCheckpoint]:
    statement = select(ProcessingCheckpoint).order_by(
        ProcessingCheckpoint.created_at.desc()
    ).limit(500)
    if raw_item_id is not None:
        statement = statement.where(
            ProcessingCheckpoint.raw_item_id == raw_item_id
        )
    return list(db.scalars(statement))


@router.get("/jobs", response_model=list[PipelineJobRead])
def list_jobs(
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
) -> list[PipelineJob]:
    statement = select(PipelineJob).order_by(PipelineJob.created_at.desc()).limit(500)
    if status_filter:
        statement = statement.where(PipelineJob.status == status_filter)
    return list(db.scalars(statement))


@router.post("/jobs/enqueue-pending")
def enqueue_pending_jobs(db: Session = Depends(get_db)) -> dict[str, int]:
    jobs = enqueue_pending_raw_items(db)
    return {"enqueued_count": len(jobs)}


@router.post(
    "/jobs/{job_id}/recover",
    response_model=PipelineCorrectionRead | PipelineJobRead,
)
async def recover_job(
    job_id: int,
    payload: PipelineCorrectionCreate,
    db: Session = Depends(get_db),
) -> PipelineCorrection | PipelineJob:
    try:
        return await recover_failed_job(db, job_id=job_id, payload=payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
