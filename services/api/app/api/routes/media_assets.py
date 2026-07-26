from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.media_asset import MediaAsset
from app.models.media_extraction import MediaExtraction
from app.schemas.media_asset import MediaAssetRead, MediaExtractionRead
from app.services.llm import LLMAnalysisError, LLMConfigurationError
from app.services.media_ocr import OCRProcessingError
from app.workflows.understand_media import extract_patch_preview

router = APIRouter()


@router.get("", response_model=list[MediaAssetRead])
def list_media_assets(
    raw_item_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[MediaAsset]:
    statement = select(MediaAsset)
    if raw_item_id is not None:
        statement = statement.where(MediaAsset.raw_item_id == raw_item_id)
    statement = statement.order_by(MediaAsset.raw_item_id, MediaAsset.block_index).limit(200)
    return list(db.scalars(statement))


@router.get("/extractions", response_model=list[MediaExtractionRead])
def list_media_extractions(
    media_asset_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[MediaExtraction]:
    statement = select(MediaExtraction)
    if media_asset_id is not None:
        statement = statement.where(MediaExtraction.media_asset_id == media_asset_id)
    return list(db.scalars(statement.order_by(MediaExtraction.created_at.desc()).limit(200)))


@router.post("/{media_asset_id}/extract-patch-preview", response_model=MediaExtractionRead)
async def process_patch_preview(
    media_asset_id: int, db: Session = Depends(get_db)
) -> MediaExtraction:
    media_asset = db.get(MediaAsset, media_asset_id)
    if not media_asset:
        raise HTTPException(status_code=404, detail="media asset not found")
    try:
        extraction = await extract_patch_preview(
            db, raw_item=media_asset.raw_item, media_asset=media_asset
        )
        db.commit()
        db.refresh(extraction)
        return extraction
    except LLMConfigurationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except (LLMAnalysisError, OCRProcessingError, RuntimeError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
