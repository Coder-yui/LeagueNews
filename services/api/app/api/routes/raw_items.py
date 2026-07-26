from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.models.raw_item import RawItem
from app.models.workflow import ProcessingRun
from app.schemas.raw_item import RawItemRead
from app.schemas.workflow import ProcessingRunRead
from app.services.llm import LLMAnalysisError, LLMConfigurationError
from app.services.media_ocr import OCRProcessingError
from app.workflows.reviewed_pipeline import start_item_processing

router = APIRouter()


@router.get("", response_model=list[RawItemRead])
def list_raw_items(db: Session = Depends(get_db)) -> list[RawItem]:
    statement = (
        select(RawItem)
        .options(
            selectinload(RawItem.source),
            selectinload(RawItem.normalized_item),
            selectinload(RawItem.processing_runs),
        )
        .order_by(RawItem.ingested_at.desc())
        .limit(100)
    )
    return list(db.scalars(statement))


@router.post("/{item_id}/process", response_model=ProcessingRunRead)
async def process_raw_item(item_id: int, db: Session = Depends(get_db)) -> object:
    raw_item = db.get(RawItem, item_id)
    if not raw_item:
        raise HTTPException(status_code=404, detail="raw item not found")
    if raw_item.normalized_item:
        raise HTTPException(status_code=409, detail="raw item already has approved analysis")
    active = db.scalar(
        select(ProcessingRun).where(
            ProcessingRun.raw_item_id == item_id,
            ProcessingRun.status.in_(["running", "awaiting_review"]),
        )
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"raw item already has active processing run {active.id}",
        )
    try:
        return await start_item_processing(db, raw_item)
    except LLMConfigurationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except (LLMAnalysisError, OCRProcessingError, RuntimeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
