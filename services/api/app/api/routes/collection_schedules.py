from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.collection_schedule import SourceCollectionSchedule
from app.models.source import Source
from app.schemas.collection_schedule import (
    CollectionScheduleRead,
    CollectionScheduleUpdate,
)
from app.services.collection_scheduler import (
    list_collection_schedules,
    request_collection_run,
    upsert_collection_schedule,
)

router = APIRouter()


def _source_or_404(db: Session, source_id: int) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    return source


@router.get("", response_model=list[CollectionScheduleRead])
def list_schedules(
    db: Session = Depends(get_db),
) -> list[SourceCollectionSchedule]:
    return list_collection_schedules(db)


@router.put(
    "/sources/{source_id}",
    response_model=CollectionScheduleRead,
)
def configure_schedule(
    source_id: int,
    payload: CollectionScheduleUpdate,
    db: Session = Depends(get_db),
) -> SourceCollectionSchedule:
    source = _source_or_404(db, source_id)
    try:
        return upsert_collection_schedule(db, source=source, payload=payload)
    except (LookupError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/sources/{source_id}/run-now",
    response_model=CollectionScheduleRead,
)
def run_source_now(
    source_id: int,
    db: Session = Depends(get_db),
) -> SourceCollectionSchedule:
    source = _source_or_404(db, source_id)
    try:
        return request_collection_run(db, source=source)
    except (LookupError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
