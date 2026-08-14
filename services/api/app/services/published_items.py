from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.query_filters import json_array_contains
from app.domain.importance import FEATURED_MESSAGE_MIN_IMPORTANCE
from app.domain.message_taxonomy import Product
from app.models.media_extraction import MediaExtraction
from app.models.normalized_item import NormalizedItem, NormalizedItemMediaExtraction
from app.models.raw_item import RawItem
from app.models.source import Source
from app.services.raw_item_versions import latest_normalized_item_condition


DEFAULT_PUBLICATION_TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True, slots=True)
class PublishedItemSearchResult:
    items: list[dict[str, Any]]
    total: int


def published_item_statement():
    """Build the eager-loaded statement used by public published-item reads."""
    return (
        select(NormalizedItem)
        .join(NormalizedItem.raw_item)
        .join(RawItem.source)
        .options(
            selectinload(NormalizedItem.raw_item).selectinload(RawItem.source),
            selectinload(NormalizedItem.raw_item).selectinload(RawItem.media_assets),
            selectinload(NormalizedItem.media_links)
            .selectinload(NormalizedItemMediaExtraction.media_extraction)
            .selectinload(MediaExtraction.media_asset),
        )
    )


def publication_time_column():
    return func.coalesce(RawItem.published_at, RawItem.ingested_at)


def publication_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("unsupported timezone") from exc


def utc_day_window(publication_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    timezone = publication_timezone(timezone_name)
    start = datetime.combine(publication_date, datetime.min.time(), tzinfo=timezone)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def published_item_conditions(
    db: Session,
    *,
    product: Product | None = None,
    message_type: str | None = None,
    topic: str | None = None,
    min_importance: float | None = None,
    featured: bool = False,
    search: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    published_date: date | None = None,
    timezone_name: str = DEFAULT_PUBLICATION_TIMEZONE,
) -> list[Any]:
    conditions: list[Any] = [
        latest_normalized_item_condition(),
        NormalizedItem.publication_status == "published",
    ]
    if message_type:
        conditions.append(NormalizedItem.message_type == message_type)
    if product:
        conditions.append(json_array_contains(db, NormalizedItem.products, product))
    if topic:
        conditions.append(json_array_contains(db, NormalizedItem.topics, topic))
    if min_importance is not None:
        conditions.append(NormalizedItem.importance_score >= min_importance)
    elif featured:
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
                    NormalizedItem.normalized_text.ilike(pattern),
                    NormalizedItem.translated_text.ilike(pattern),
                    RawItem.author_name.ilike(pattern),
                    Source.name.ilike(pattern),
                )
            )
    publication_time = publication_time_column()
    if since is not None:
        conditions.append(publication_time >= _as_utc(since))
    if until is not None:
        conditions.append(publication_time < _as_utc(until))
    if published_date is not None:
        start, end = utc_day_window(published_date, timezone_name)
        conditions.extend((publication_time >= start, publication_time < end))
    return conditions


def search_published_items(
    db: Session,
    *,
    query: str | None = None,
    product: Product | None = None,
    message_type: str | None = None,
    topic: str | None = None,
    min_importance: float | None = None,
    featured: bool = False,
    since: datetime | None = None,
    until: datetime | None = None,
    published_date: date | None = None,
    timezone_name: str = DEFAULT_PUBLICATION_TIMEZONE,
    sort_by: str = "time",
    sort: str = "desc",
    limit: int = 25,
    offset: int = 0,
) -> PublishedItemSearchResult:
    conditions = published_item_conditions(
        db,
        product=product,
        message_type=message_type,
        topic=topic,
        min_importance=min_importance,
        featured=featured,
        search=query,
        since=since,
        until=until,
        published_date=published_date,
        timezone_name=timezone_name,
    )
    total = db.scalar(
        select(func.count(NormalizedItem.id))
        .join(NormalizedItem.raw_item)
        .join(RawItem.source)
        .where(*conditions)
    ) or 0
    sort_column = {
        "time": publication_time_column(),
        "importance": NormalizedItem.importance_score,
        "priority": NormalizedItem.priority_score,
        "intrinsic": NormalizedItem.importance_score,
    }.get(sort_by)
    if sort_column is None:
        raise ValueError("unsupported published-item sort")
    ordering = (
        (sort_column.asc(), NormalizedItem.id.asc())
        if sort == "asc"
        else (sort_column.desc(), NormalizedItem.id.desc())
    )
    statement = (
        published_item_statement()
        .where(*conditions)
        .order_by(*ordering)
        .offset(offset)
        .limit(limit)
    )
    return PublishedItemSearchResult(
        items=[published_item_payload(item) for item in db.scalars(statement)],
        total=int(total),
    )


def get_published_item(db: Session, item_id: int) -> dict[str, Any] | None:
    item = db.scalar(
        published_item_statement().where(
            NormalizedItem.id == item_id,
            latest_normalized_item_condition(),
            NormalizedItem.publication_status == "published",
        )
    )
    return published_item_payload(item) if item is not None else None


def list_published_days(
    db: Session,
    *,
    product: Product | None = None,
    message_type: str | None = None,
    featured: bool = False,
    search: str | None = None,
    timezone_name: str = DEFAULT_PUBLICATION_TIMEZONE,
    limit: int = 30,
) -> dict[str, Any]:
    """List newest publication dates with counts in the requested civil timezone."""
    timezone = publication_timezone(timezone_name)
    conditions = published_item_conditions(
        db,
        product=product,
        message_type=message_type,
        featured=featured,
        search=search,
    )
    publication_time = publication_time_column()
    timestamps = db.scalars(
        select(publication_time)
        .select_from(NormalizedItem)
        .join(NormalizedItem.raw_item)
        .join(RawItem.source)
        .where(*conditions)
        .order_by(publication_time.desc(), NormalizedItem.id.desc())
        .execution_options(yield_per=500)
    )

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


def published_item_payload(item: NormalizedItem) -> dict[str, Any]:
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
                # Only the published path is part of the public projection. The
                # local storage_path is intentionally never returned here.
                "storage_path": asset.public_path,
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
        "original_content_blocks": public_content_blocks(
            raw_item.content_blocks, public_media_by_index
        ),
        "source_language": item.source_language,
        "translated_title": item.translated_title,
        "translated_content_blocks": public_content_blocks(
            item.translated_content_blocks, public_media_by_index
        ),
        "translation_status": item.translation_status,
        "media_extractions": media_extractions,
        "created_at": item.created_at,
    }


def public_content_blocks(
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
        else:
            # Non-image storage paths are internal implementation details and
            # are not useful to public consumers.
            copied.pop("storage_path", None)
        result.append(copied)
    return result
