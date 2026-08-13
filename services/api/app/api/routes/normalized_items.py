from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.query_filters import json_array_contains
from app.core.database import get_db
from app.domain.importance import FEATURED_MESSAGE_MIN_IMPORTANCE
from app.domain.message_taxonomy import MESSAGE_TYPE_ORDER, PRODUCTS, TOPIC_RULES, Product
from app.models.media_extraction import MediaExtraction
from app.models.normalized_item import NormalizedItem, NormalizedItemMediaExtraction
from app.models.raw_item import RawItem
from app.schemas.normalized_item import (
    NormalizedItemRead,
    PublishedDayListRead,
    PublishedItemPageRead,
    PublishedItemRead,
)
from app.services.raw_item_versions import latest_normalized_item_condition

router = APIRouter()

DEFAULT_PUBLICATION_TIMEZONE = "Asia/Shanghai"


@router.get("", response_model=list[NormalizedItemRead])
def list_normalized_items(db: Session = Depends(get_db)) -> list[NormalizedItem]:
    statement = (
        select(NormalizedItem)
        .where(
            latest_normalized_item_condition(),
            NormalizedItem.publication_status == "published",
        )
        .order_by(NormalizedItem.created_at.desc())
        .limit(100)
    )
    return list(db.scalars(statement))


def _published_statement():
    return (
        select(NormalizedItem)
        .join(NormalizedItem.raw_item)
        .options(
            selectinload(NormalizedItem.raw_item).selectinload(RawItem.source),
            selectinload(NormalizedItem.raw_item).selectinload(RawItem.media_assets),
            selectinload(NormalizedItem.media_links)
            .selectinload(NormalizedItemMediaExtraction.media_extraction)
            .selectinload(MediaExtraction.media_asset),
        )
    )


def _publication_time_column():
    return func.coalesce(RawItem.published_at, RawItem.ingested_at)


def _publication_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="unsupported timezone") from exc


def _utc_day_window(publication_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    timezone = _publication_timezone(timezone_name)
    start = datetime.combine(publication_date, datetime.min.time(), tzinfo=timezone)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def _published_conditions(
    db: Session,
    *,
    product: Product | None = None,
    message_type: str | None = None,
    featured: bool = False,
    search: str | None = None,
) -> list[Any]:
    conditions: list[Any] = [
        latest_normalized_item_condition(),
        NormalizedItem.publication_status == "published",
    ]
    if message_type:
        conditions.append(NormalizedItem.message_type == message_type)
    if product:
        conditions.append(json_array_contains(db, NormalizedItem.products, product))
    if featured:
        conditions.append(NormalizedItem.importance_score >= FEATURED_MESSAGE_MIN_IMPORTANCE)
    if search:
        search_value = search.strip()
        if search_value.isdigit():
            conditions.append(NormalizedItem.id == int(search_value))
        elif search_value:
            pattern = f"%{search_value}%"
            conditions.append(
                or_(
                    NormalizedItem.normalized_title.ilike(pattern),
                    NormalizedItem.translated_title.ilike(pattern),
                    NormalizedItem.summary.ilike(pattern),
                )
            )
    return conditions


def _published_payload(item: NormalizedItem) -> dict[str, Any]:
    raw_item = item.raw_item
    source = raw_item.source
    public_media_by_index = {
        asset.block_index: asset.public_path for asset in raw_item.media_assets if asset.public_path
    }
    media_extractions = []
    for link in sorted(
        item.media_links,
        key=lambda value: (
            value.media_extraction.media_asset.block_index,
            value.media_extraction_id,
        ),
    ):
        extraction = link.media_extraction
        asset = extraction.media_asset
        media_extractions.append(
            {
                "extraction_id": extraction.id,
                "media_asset_id": asset.id,
                "block_index": asset.block_index,
                "storage_path": asset.storage_path,
                "source_url": asset.source_url,
                "mime_type": asset.mime_type,
                "confidence": extraction.confidence,
                "original_data": extraction.structured_data,
                "translated_data": link.translated_structured_data,
            }
        )
    return {
        "id": item.id,
        "raw_item_id": item.raw_item_id,
        "title": item.translated_title or item.normalized_title,
        "summary": item.summary,
        "entities": item.entities,
        "products": item.products,
        "message_type": item.message_type,
        "topics": item.topics,
        "classification_version": item.classification_version,
        "content_form": item.content_form,
        "facets": item.facets,
        "importance_score": item.importance_score,
        "importance_dimensions": item.importance_dimensions,
        "importance_policy_version": item.importance_policy_version,
        "priority_score": item.priority_score,
        "source_id": source.id,
        "source_name": source.name,
        "source_reliability_score": source.reliability_score,
        "source_base_url": source.base_url,
        "source_url": raw_item.canonical_url,
        "author": raw_item.author_name,
        "published_at": raw_item.published_at,
        "original_title": raw_item.display_title,
        "original_content_blocks": _public_blocks(raw_item.content_blocks, public_media_by_index),
        "source_language": item.source_language,
        "translated_title": item.translated_title,
        "translated_content_blocks": _public_blocks(
            item.translated_content_blocks, public_media_by_index
        ),
        "translation_status": item.translation_status,
        "media_extractions": media_extractions,
        "created_at": item.created_at,
    }


def _public_blocks(
    blocks: list[dict[str, Any]],
    public_media_by_index: dict[int, str],
) -> list[dict[str, Any]]:
    result = []
    for index, block in enumerate(blocks):
        copied = dict(block)
        if copied.get("type") == "image":
            public_path = public_media_by_index.get(index)
            if public_path:
                copied["storage_path"] = public_path
            else:
                copied.pop("storage_path", None)
        result.append(copied)
    return result


@router.get("/published", response_model=list[PublishedItemRead])
def list_published_items(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    statement = (
        _published_statement()
        .where(
            latest_normalized_item_condition(),
            NormalizedItem.publication_status == "published",
        )
        .order_by(
            func.coalesce(RawItem.published_at, RawItem.ingested_at).desc(),
            NormalizedItem.id.desc(),
        )
        .limit(100)
    )
    return [_published_payload(item) for item in db.scalars(statement)]


@router.get("/published-page", response_model=PublishedItemPageRead)
def list_published_items_page(
    product: Product | None = None,
    message_type: str | None = None,
    featured: bool = False,
    search: str | None = None,
    published_date: Annotated[date | None, Query(alias="date")] = None,
    timezone_name: Annotated[str, Query(alias="timezone")] = DEFAULT_PUBLICATION_TIMEZONE,
    sort_by: str = Query(
        default="time",
        pattern="^(time|importance|priority|intrinsic)$",
    ),
    sort: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conditions = _published_conditions(
        db,
        product=product,
        message_type=message_type,
        featured=featured,
        search=search,
    )
    if published_date is not None:
        start, end = _utc_day_window(published_date, timezone_name)
        publication_time = _publication_time_column()
        conditions.extend((publication_time >= start, publication_time < end))
    count_statement = (
        select(func.count(NormalizedItem.id)).join(NormalizedItem.raw_item).where(*conditions)
    )
    total = db.scalar(count_statement) or 0
    sort_column = {
        "time": func.coalesce(RawItem.published_at, RawItem.ingested_at),
        "importance": NormalizedItem.importance_score,
        "priority": NormalizedItem.priority_score,
        "intrinsic": NormalizedItem.importance_score,
    }[sort_by]
    ordering = (
        (sort_column.asc(), NormalizedItem.id.asc())
        if sort == "asc"
        else (sort_column.desc(), NormalizedItem.id.desc())
    )
    statement = (
        _published_statement().where(*conditions).order_by(*ordering).offset(offset).limit(limit)
    )
    return {
        "items": [_published_payload(item) for item in db.scalars(statement)],
        "total": total,
        "product_options": list(PRODUCTS),
        "message_type_options": list(MESSAGE_TYPE_ORDER),
        "topic_options": [rule.code for rule in TOPIC_RULES],
    }


@router.get("/published-days", response_model=PublishedDayListRead)
def list_published_days(
    product: Product | None = None,
    message_type: str | None = None,
    featured: bool = False,
    search: str | None = None,
    timezone_name: Annotated[str, Query(alias="timezone")] = DEFAULT_PUBLICATION_TIMEZONE,
    limit: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """List newest publication dates with counts in the requested civil timezone."""
    timezone = _publication_timezone(timezone_name)
    conditions = _published_conditions(
        db,
        product=product,
        message_type=message_type,
        featured=featured,
        search=search,
    )
    publication_time = _publication_time_column()
    timestamp_statement = (
        select(publication_time)
        .select_from(NormalizedItem)
        .join(NormalizedItem.raw_item)
        .where(*conditions)
        .order_by(publication_time.desc(), NormalizedItem.id.desc())
        .execution_options(yield_per=500)
    )
    timestamps = db.scalars(timestamp_statement)

    days: list[dict[str, Any]] = []
    for value in timestamps:
        timestamp = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        local_date = timestamp.astimezone(timezone).date()
        if days and days[-1]["date"] == local_date:
            days[-1]["count"] += 1
        elif len(days) >= limit:
            break
        else:
            days.append(
                {
                    "date": local_date,
                    "count": 1,
                    "latest_published_at": timestamp,
                }
            )
    return {"days": days, "timezone": timezone_name}


@router.get("/{item_id}/published", response_model=PublishedItemRead)
def get_published_item(
    item_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = db.scalar(
        _published_statement().where(
            NormalizedItem.id == item_id,
            latest_normalized_item_condition(),
            NormalizedItem.publication_status == "published",
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="published item not found")
    return _published_payload(item)
