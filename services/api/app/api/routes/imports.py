from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import ConnectorRequest, ConnectorSource
from app.connectors.manual import ManualConnector
from app.core.database import get_db
from app.models.raw_item import RawItem
from app.models.source import Source
from app.schemas.raw_item import RawItemImport, RawItemRead
from app.services.ingestion import ingest_connector_items

router = APIRouter()


@router.post("/manual", response_model=RawItemRead, status_code=status.HTTP_201_CREATED)
async def manual_import(payload: RawItemImport, db: Session = Depends(get_db)) -> RawItem:
    source = db.get(Source, payload.source_id) if payload.source_id else None
    if payload.source_id and not source:
        raise HTTPException(status_code=404, detail="source not found")
    if not source:
        source = db.scalar(select(Source).where(Source.connector_type == "manual"))
    if not source:
        source = Source(name="手动导入", connector_type="manual")
        db.add(source)
        db.flush()

    source_context = ConnectorSource(
        id=source.id,
        name=source.name,
        connector_type=source.connector_type,
        external_key=source.external_key,
        base_url=source.base_url,
        connector_config=source.connector_config,
    )
    items = await ManualConnector().collect(
        ConnectorRequest(
            source=source_context,
            limit=1,
            since=None,
            options={
                "title": payload.title,
                "url": str(payload.url) if payload.url else None,
                "content": payload.content,
                "content_blocks": (
                    [
                        block.model_dump(mode="json", exclude_none=True)
                        for block in payload.content_blocks
                    ]
                    if payload.content_blocks
                    else []
                ),
                "raw_payload": payload.raw_payload
                or payload.model_dump(mode="json", exclude_none=True),
                "author": payload.author,
                "language": payload.language,
                "external_id": payload.external_id,
                "published_at": payload.published_at,
            },
        )
    )
    result = await ingest_connector_items(
        db,
        source=source,
        items=items,
    )
    if not result.created:
        existing = result.skipped[0]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "duplicate raw item",
                "existing_raw_item_id": existing.id,
            },
        )
    return result.created[0]
