import asyncio
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.media_asset import MediaAsset
from app.models.ocr_lab import OCRProfile, OCRTestRun
from app.models.raw_item import RawItem
from app.models.source import Source
from app.schemas.ocr_lab import OCRAssetRead, OCRTestRequest
from app.services.media_ocr import render_ocr_overlay, run_ocr
from app.services.patch_table import parse_patch_table, render_patch_table_overlay


def list_patch_ocr_assets(db: Session) -> list[OCRAssetRead]:
    rows = db.execute(
        select(MediaAsset, RawItem)
        .join(RawItem, RawItem.id == MediaAsset.raw_item_id)
        .join(Source, Source.id == RawItem.source_id)
        .where(
            Source.connector_type == "x_twitter",
            Source.external_key == "riotphroxzon",
            MediaAsset.storage_path.is_not(None),
        )
        .order_by(RawItem.published_at.desc(), MediaAsset.block_index.asc())
    ).all()
    return [
        OCRAssetRead(
            media_asset_id=asset.id,
            raw_item_id=raw.id,
            raw_title=raw.display_title,
            published_at=raw.published_at,
            block_index=asset.block_index,
            storage_path=str(asset.storage_path),
            source_url=asset.source_url,
            width=asset.width,
            height=asset.height,
        )
        for asset, raw in rows
    ]


async def create_ocr_test_run(
    db: Session,
    payload: OCRTestRequest,
) -> OCRTestRun:
    asset = db.get(MediaAsset, payload.media_asset_id)
    if not asset:
        raise LookupError(f"media asset not found: {payload.media_asset_id}")
    if not asset.storage_path:
        raise ValueError("media asset has no local storage_path")
    if asset.mime_type and not asset.mime_type.startswith("image/"):
        raise ValueError("media asset is not an image")
    parameters = payload.parameters.model_dump(mode="json")
    result = await asyncio.to_thread(run_ocr, asset.storage_path, parameters)
    table = await asyncio.to_thread(
        parse_patch_table,
        asset.storage_path,
        result,
        parameters,
        title_hint=asset.raw_item.display_title,
    )

    overlay_name = f"{asset.id}-{uuid4().hex}.jpg"
    overlay_path = settings.resolved_media_root / "ocr_lab" / overlay_name
    public_overlay_path = f"/media/ocr_lab/{overlay_name}"
    table_overlay_name = f"{asset.id}-{uuid4().hex}-table.jpg"
    table_overlay_path = settings.resolved_media_root / "ocr_lab" / table_overlay_name
    public_table_overlay_path = f"/media/ocr_lab/{table_overlay_name}"
    try:
        await asyncio.gather(
            asyncio.to_thread(
                render_ocr_overlay,
                asset.storage_path,
                result,
                parameters,
                overlay_path,
            ),
            asyncio.to_thread(
                render_patch_table_overlay,
                asset.storage_path,
                table,
                parameters,
                table_overlay_path,
            ),
        )
        test_run = OCRTestRun(
            media_asset_id=asset.id,
            profile_name=payload.profile_name,
            parameters=parameters,
            status="completed",
            raw_text=result.raw_text,
            lines=result.lines,
            confidence=result.confidence,
            source_width=result.width,
            source_height=result.height,
            processed_width=result.processed_width,
            processed_height=result.processed_height,
            overlay_path=public_overlay_path,
            table_overlay_path=public_table_overlay_path,
            table_data=table.model_dump(),
            structure_confidence=table.structure_confidence,
            engine=result.engine,
        )
        asset.width = result.width
        asset.height = result.height
        db.add(test_run)
        db.commit()
        db.refresh(test_run)
        return test_run
    except BaseException:
        db.rollback()
        _remove_overlay(overlay_path)
        _remove_overlay(table_overlay_path)
        raise


def activate_ocr_test_run(db: Session, test_run: OCRTestRun) -> OCRProfile:
    db.execute(update(OCRProfile).values(is_active=False))
    profile = OCRProfile(
        name=test_run.profile_name,
        parameters=test_run.parameters,
        source_test_run_id=test_run.id,
        is_active=True,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def _remove_overlay(path: Path) -> None:
    try:
        if path.is_file() and path.is_relative_to(settings.resolved_media_root):
            path.unlink()
    except OSError:
        pass
