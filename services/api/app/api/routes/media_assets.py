from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.media_extraction import MediaExtraction
from app.models.media_asset import MediaAsset
from app.schemas.media_asset import MediaAssetRead, MediaExtractionRead

router = APIRouter()


@router.get("/files/{namespace}/{filename}")
def get_private_media(
    namespace: str,
    filename: str,
    db: Session = Depends(get_db),
) -> FileResponse:
    if Path(namespace).name != namespace or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="media asset not found")
    api_path = f"/api/v1/media-assets/files/{namespace}/{filename}"
    asset = db.scalar(
        select(MediaAsset).where(MediaAsset.storage_path == api_path).limit(1)
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="media asset not found")
    root = settings.resolved_media_root.resolve()
    path = (root / "private" / namespace / filename).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="media asset not found")
    return FileResponse(path, media_type=asset.mime_type)


@router.get("/published/{namespace}/{filename}")
def get_published_media(
    namespace: str,
    filename: str,
    db: Session = Depends(get_db),
) -> FileResponse:
    if Path(namespace).name != namespace or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="published media not found")
    public_path = f"/media/published/{namespace}/{filename}"
    asset = db.scalar(
        select(MediaAsset).where(
            MediaAsset.public_path == public_path,
            MediaAsset.visibility == "published",
        ).limit(1)
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="published media not found")
    root = settings.resolved_media_root.resolve()
    path = (root / "published" / namespace / filename).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="published media not found")
    return FileResponse(path, media_type=asset.mime_type)


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
