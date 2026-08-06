from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.models.media_extraction import MediaExtraction
from app.models.event import EventMessage
from app.models.normalized_item import NormalizedItem, NormalizedItemMediaExtraction
from app.models.raw_item import RawItem
from app.schemas.normalized_item import (
    NormalizedItemRead,
    PublishedItemPageRead,
    PublishedItemRead,
)
from app.services.raw_item_versions import latest_normalized_item_condition

router = APIRouter()


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
    return select(NormalizedItem).join(NormalizedItem.raw_item).options(
        selectinload(NormalizedItem.raw_item).selectinload(RawItem.source),
        selectinload(NormalizedItem.media_links)
        .selectinload(NormalizedItemMediaExtraction.media_extraction)
        .selectinload(MediaExtraction.media_asset),
        selectinload(NormalizedItem.claims),
        selectinload(NormalizedItem.event_memberships).selectinload(EventMessage.event),
    )


def _published_payload(item: NormalizedItem) -> dict[str, Any]:
    raw_item = item.raw_item
    source = raw_item.source
    public_media_by_index = {
        asset.block_index: asset.public_path
        for asset in raw_item.media_assets
        if asset.public_path
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
        "category": item.category,
        "entities": item.entities,
        "content_type": item.content_type,
        "primary_topic": item.primary_topic,
        "secondary_topics": item.secondary_topics,
        "facets": item.facets,
        "ontology_version": item.ontology_version,
        "importance_score": item.importance_score,
        "importance_dimensions": item.importance_dimensions,
        "importance_policy_version": item.importance_policy_version,
        "credibility": item.credibility,
        "credibility_score": item.credibility_score,
        "credibility_evidence": item.credibility_evidence,
        "credibility_components": item.credibility_components,
        "credibility_policy_version": item.credibility_policy_version,
        "source_id": source.id,
        "source_name": source.name,
        "source_base_url": source.base_url,
        "source_url": raw_item.canonical_url,
        "author": raw_item.author_name,
        "published_at": raw_item.published_at,
        "original_title": raw_item.display_title,
        "original_content_blocks": _public_blocks(
            raw_item.content_blocks, public_media_by_index
        ),
        "source_language": item.source_language,
        "translated_title": item.translated_title,
        "translated_content_blocks": _public_blocks(
            item.translated_content_blocks, public_media_by_index
        ),
        "translation_status": item.translation_status,
        "media_extractions": media_extractions,
        "fact_claims": [
            {
                "id": claim.id,
                "subject": claim.subject,
                "predicate": claim.predicate,
                "object_value": claim.object_value,
                "attribution": claim.attribution,
                "stance": claim.stance,
                "confidence": claim.confidence,
            }
            for claim in item.claims
            if claim.status == "active"
        ],
        "event_memberships": [
            {
                "event_id": membership.event_id,
                "event_title": membership.event.title,
                "event_type": membership.event.event_type,
                "membership_role": membership.membership_role,
                "evidence_stance": membership.evidence_stance,
            }
            for membership in item.event_memberships
            if membership.membership_status == "active"
        ],
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
    primary_topic: str | None = None,
    content_type: str | None = None,
    minimum_credibility: float = Query(default=0, ge=0, le=1),
    search: str | None = None,
    sort_by: str = Query(default="time", pattern="^(time|credibility|importance)$"),
    sort: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conditions = [
        latest_normalized_item_condition(),
        NormalizedItem.publication_status == "published",
        NormalizedItem.credibility_score >= minimum_credibility,
    ]
    if primary_topic:
        conditions.append(NormalizedItem.primary_topic == primary_topic)
    if content_type:
        conditions.append(
            NormalizedItem.content_type.is_(None)
            if content_type == "null"
            else NormalizedItem.content_type == content_type
        )
    if search:
        search_value = search.strip()
        if search_value.isdigit():
            conditions.append(NormalizedItem.id == int(search_value))
        else:
            pattern = f"%{search_value}%"
            conditions.append(
                or_(
                    NormalizedItem.normalized_title.ilike(pattern),
                    NormalizedItem.translated_title.ilike(pattern),
                    NormalizedItem.summary.ilike(pattern),
                )
            )
    count_statement = (
        select(func.count(NormalizedItem.id))
        .join(NormalizedItem.raw_item)
        .where(*conditions)
    )
    total = db.scalar(count_statement) or 0
    sort_column = {
        "time": func.coalesce(RawItem.published_at, RawItem.ingested_at),
        "credibility": NormalizedItem.credibility_score,
        "importance": NormalizedItem.importance_score,
    }[sort_by]
    ordering = (
        (sort_column.asc(), NormalizedItem.id.asc())
        if sort == "asc"
        else (sort_column.desc(), NormalizedItem.id.desc())
    )
    statement = (
        _published_statement()
        .where(*conditions)
        .order_by(*ordering)
        .offset(offset)
        .limit(limit)
    )
    option_conditions = [
        latest_normalized_item_condition(),
        NormalizedItem.publication_status == "published",
    ]
    topic_options = [
        value
        for value in db.scalars(
            select(NormalizedItem.primary_topic)
            .where(*option_conditions)
            .distinct()
            .order_by(NormalizedItem.primary_topic)
        )
        if value
    ]
    content_type_options = [
        value
        for value in db.scalars(
            select(NormalizedItem.content_type)
            .where(*option_conditions)
            .distinct()
            .order_by(NormalizedItem.content_type)
        )
        if value
    ]
    if db.scalar(
        select(func.count(NormalizedItem.id)).where(
            *option_conditions,
            NormalizedItem.content_type.is_(None),
        )
    ):
        content_type_options.append("null")
    return {
        "items": [_published_payload(item) for item in db.scalars(statement)],
        "total": total,
        "topic_options": topic_options,
        "content_type_options": content_type_options,
    }


@router.get("/{item_id}/published", response_model=PublishedItemRead)
def get_published_item(
    item_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = db.scalar(
        _published_statement().where(
            NormalizedItem.id == item_id,
            NormalizedItem.publication_status == "published",
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="published item not found")
    return _published_payload(item)
