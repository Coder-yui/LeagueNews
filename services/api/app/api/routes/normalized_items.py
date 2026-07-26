from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.normalized_item import NormalizedItem
from app.schemas.normalized_item import NormalizedItemRead
from app.schemas.workflow import ProcessingRunRead
from app.services.llm import LLMAnalysisError, LLMConfigurationError
from app.workflows.translate_item import translate_normalized_item
from app.workflows.reviewed_pipeline import start_event_processing

router = APIRouter()


@router.get("", response_model=list[NormalizedItemRead])
def list_normalized_items(db: Session = Depends(get_db)) -> list[NormalizedItem]:
    statement = select(NormalizedItem).order_by(NormalizedItem.created_at.desc()).limit(100)
    return list(db.scalars(statement))


@router.post("/{item_id}/process-event", response_model=ProcessingRunRead)
async def process_normalized_item_event(
    item_id: int, db: Session = Depends(get_db)
) -> object:
    item = db.get(NormalizedItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="normalized item not found")
    try:
        return await start_event_processing(db, item)
    except LLMConfigurationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except (LLMAnalysisError, RuntimeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/{item_id}/translate", response_model=NormalizedItemRead)
async def translate_item(item_id: int, db: Session = Depends(get_db)) -> NormalizedItem:
    item = db.get(NormalizedItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="normalized item not found")
    try:
        return await translate_normalized_item(db, item)
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except LLMAnalysisError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
