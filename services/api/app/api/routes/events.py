from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.models.event_item import EventItem
from app.models.media_asset import MediaAsset
from app.models.news_event import NewsEvent
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.schemas.event_feed import EventFeedItem, EventFeedRead, EventMediaExtraction
from app.schemas.news_event import NewsEventRead

router = APIRouter()


@router.get("/feed", response_model=list[EventFeedRead])
def list_event_feed(db: Session = Depends(get_db)) -> list[EventFeedRead]:
    statement = (
        select(NewsEvent)
        .options(
            selectinload(NewsEvent.items)
            .selectinload(EventItem.normalized_item)
            .selectinload(NormalizedItem.raw_item)
            .selectinload(RawItem.source)
            ,
            selectinload(NewsEvent.items)
            .selectinload(EventItem.normalized_item)
            .selectinload(NormalizedItem.raw_item)
            .selectinload(RawItem.media_assets)
            .selectinload(MediaAsset.extractions)
        )
        .order_by(NewsEvent.importance_score.desc(), NewsEvent.created_at.desc())
        .limit(100)
    )
    events = list(db.scalars(statement))
    return [
        EventFeedRead(
            id=event.id,
            title=event.title,
            summary=event.summary,
            category=event.category,
            entities=event.entities,
            importance_score=event.importance_score,
            credibility=event.credibility,
            occurred_at=event.occurred_at,
            created_at=event.created_at,
            items=[
                EventFeedItem(
                    normalized_item_id=link.normalized_item.id,
                    raw_item_id=link.normalized_item.raw_item.id,
                    source_id=link.normalized_item.raw_item.source.id,
                    source_name=link.normalized_item.raw_item.source.name,
                    source_base_url=link.normalized_item.raw_item.source.base_url,
                    source_url=link.normalized_item.raw_item.canonical_url,
                    author=link.normalized_item.raw_item.author_name,
                    published_at=link.normalized_item.raw_item.published_at,
                    original_title=link.normalized_item.raw_item.display_title,
                    original_content_blocks=link.normalized_item.raw_item.content_blocks,
                    source_language=link.normalized_item.source_language,
                    translated_title=link.normalized_item.translated_title,
                    translated_content_blocks=link.normalized_item.translated_content_blocks,
                    translation_status=link.normalized_item.translation_status,
                    media_extractions=[
                        EventMediaExtraction(
                            media_asset_id=asset.id,
                            storage_path=asset.storage_path,
                            task_type=extraction.task_type,
                            status=extraction.status,
                            confidence=extraction.confidence,
                            structured_data=extraction.structured_data,
                        )
                        for asset in link.normalized_item.raw_item.media_assets
                        for extraction in asset.extractions
                        if extraction.status == "processed"
                        and (
                            extraction.id
                            in link.normalized_item.approved_media_extraction_ids
                            or (
                                not link.normalized_item.approved_media_extraction_ids
                                and link.normalized_item.analysis_version != "v3-reviewed"
                            )
                        )
                    ],
                )
                for link in event.items
            ],
        )
        for event in events
    ]


@router.get("", response_model=list[NewsEventRead])
def list_events(db: Session = Depends(get_db)) -> list[NewsEvent]:
    statement = select(NewsEvent).order_by(
        NewsEvent.importance_score.desc(), NewsEvent.created_at.desc()
    ).limit(100)
    return list(db.scalars(statement))
