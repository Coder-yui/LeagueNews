from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.media_extraction import MediaExtraction
from app.models.media_asset import MediaAsset
from app.schemas.media_asset import MediaAssetRead, MediaExtractionRead

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
