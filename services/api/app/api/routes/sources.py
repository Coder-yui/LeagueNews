from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.source import Source
from app.schemas.source import SourceCreate, SourceRead, SourceReliabilityUpdate

router = APIRouter()


@router.get("", response_model=list[SourceRead])
def list_sources(db: Session = Depends(get_db)) -> list[Source]:
    return list(db.scalars(select(Source).order_by(Source.name)))


@router.post("", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
def create_source(payload: SourceCreate, db: Session = Depends(get_db)) -> Source:
    if db.scalar(select(Source).where(Source.name == payload.name)):
        raise HTTPException(status_code=409, detail="source name already exists")
    if payload.external_key and db.scalar(
        select(Source).where(
            Source.connector_type == payload.connector_type,
            Source.external_key == payload.external_key,
        )
    ):
        raise HTTPException(
            status_code=409,
            detail="source connector_type/external_key already exists",
        )
    source = Source(**payload.model_dump(mode="json"))
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.patch("/{source_id}/reliability", response_model=SourceRead)
def update_source_reliability(
    source_id: int,
    payload: SourceReliabilityUpdate,
    db: Session = Depends(get_db),
) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    source.is_official = payload.is_official
    source.reliability_score = payload.reliability_score
    db.commit()
    db.refresh(source)
    return source
