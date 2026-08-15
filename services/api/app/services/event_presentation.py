from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session, selectinload

from app.domain.event_heat import heat_level
from app.domain.event_importance import importance_level
from app.domain.event_categories import event_category
from app.models.event import Event, EventMention
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.repositories.events import current_event_mention_conditions
from app.services.published_items import _is_public_static_media_path


def _source_payload(item: NormalizedItem | None) -> dict[str, Any] | None:
    if item is None:
        return None
    raw = item.raw_item
    return {
        "message_id": item.id,
        "source_id": raw.source_id,
        "source_name": raw.source.name,
        "source_url": raw.canonical_url,
        "published_at": raw.published_at,
    }


def _best_media_url(item: NormalizedItem | None) -> str | None:
    if item is None:
        return None
    assets = sorted(item.raw_item.media_assets, key=lambda value: (value.block_index, value.id))
    for asset in assets:
        if asset.public_path:
            return asset.public_path
        if _is_public_static_media_path(asset.storage_path):
            return asset.storage_path
    return None


def event_reference_items(
    db: Session, events: list[Event]
) -> dict[int, NormalizedItem]:
    message_ids = {
        message_id
        for event in events
        for message_id in (
            event.primary_source_message_id,
            event.best_media_message_id,
        )
        if message_id is not None
    }
    if not message_ids:
        return {}
    items = db.scalars(
        select(NormalizedItem)
        .join(EventMention, EventMention.normalized_item_id == NormalizedItem.id)
        .where(
            EventMention.event_id.in_([event.id for event in events]),
            NormalizedItem.id.in_(message_ids),
            *current_event_mention_conditions(),
        )
        .options(
            selectinload(NormalizedItem.raw_item).selectinload(RawItem.source),
            selectinload(NormalizedItem.raw_item).selectinload(RawItem.media_assets),
        )
    )
    return {item.id: item for item in items}


def _event_message_counts(db: Session, event_id: int) -> tuple[int, int]:
    rows = db.execute(
        select(EventMention.normalized_item_id, RawItem.source_id)
        .join(EventMention.normalized_item)
        .join(RawItem, RawItem.id == NormalizedItem.raw_item_id)
        .where(
            EventMention.event_id == event_id,
            *current_event_mention_conditions(),
        )
    )
    message_ids: set[int] = set()
    source_ids: set[int] = set()
    for message_id, source_id in rows:
        message_ids.add(message_id)
        source_ids.add(source_id)
    return len(message_ids), len(source_ids)


def event_message_counts(
    db: Session, event_ids: list[int]
) -> dict[int, tuple[int, int]]:
    if not event_ids:
        return {}
    rows = db.execute(
        select(
            EventMention.event_id,
            func.count(distinct(EventMention.normalized_item_id)),
            func.count(distinct(RawItem.source_id)),
        )
        .join(EventMention.normalized_item)
        .join(RawItem, RawItem.id == NormalizedItem.raw_item_id)
        .where(
            EventMention.event_id.in_(event_ids),
            *current_event_mention_conditions(),
        )
        .group_by(EventMention.event_id)
    )
    return {
        event_id: (message_count, source_count)
        for event_id, message_count, source_count in rows
    }


def event_card_payload(
    db: Session,
    event: Event,
    *,
    counts: tuple[int, int] | None = None,
    reference_items: dict[int, NormalizedItem] | None = None,
) -> dict[str, Any]:
    message_count, source_count = counts or _event_message_counts(db, event.id)
    references = reference_items or {}
    primary_item = references.get(event.primary_source_message_id or 0)
    media_item = references.get(event.best_media_message_id or 0)
    if reference_items is None:
        references = event_reference_items(db, [event])
        primary_item = references.get(event.primary_source_message_id or 0)
        media_item = references.get(event.best_media_message_id or 0)
    return {
        "id": event.id,
        "title": event.title,
        "current_summary": event.current_summary,
        "products": event.products,
        "event_family": event.event_family,
        "category": event_category(event_family=event.event_family, products=event.products),
        "message_count": message_count,
        "source_count": source_count,
        "lifecycle_status": event.lifecycle_status,
        "importance_score": event.importance_score,
        "importance_level": str(
            event.importance_breakdown.get("level")
            or importance_level(event.importance_score)
        ),
        "credibility_score": event.credibility_score,
        "credibility_level": event.credibility_level,
        "heat_score": event.heat_score,
        "heat_level": str(event.heat_breakdown.get("level") or heat_level(event.heat_score)),
        "message_count_total": event.message_count_total,
        "message_count_24h": event.message_count_24h,
        "unique_sources_24h": event.unique_sources_24h,
        "last_material_update_at": event.last_material_update_at,
        "primary_source": _source_payload(primary_item),
        "best_media_url": _best_media_url(media_item),
    }


def event_detail_payload(db: Session, event: Event) -> dict[str, Any]:
    mentions = list(
        db.scalars(
            select(EventMention)
            .join(EventMention.normalized_item)
            .options(
                selectinload(EventMention.normalized_item)
                .selectinload(NormalizedItem.raw_item)
                .selectinload(RawItem.source)
            )
            .where(
                EventMention.event_id == event.id,
                *current_event_mention_conditions(),
            )
            .order_by(EventMention.source_published_at, EventMention.added_at, EventMention.id)
        )
    )
    evidence = []
    timeline = []
    related_by_id: dict[int, dict[str, Any]] = {}
    for mention in mentions:
        item = mention.normalized_item
        raw = item.raw_item
        source = raw.source
        published_at = raw.published_at or raw.ingested_at
        evidence.append(
            {
                "mention_id": mention.id,
                "message_id": item.id,
                "message_revision": mention.normalized_item_revision,
                "relation": mention.relation,
                "source_role": mention.source_role,
                "materiality": mention.materiality,
                "independence_group": mention.independence_group,
                "evidence_excerpt": mention.evidence_excerpt,
                "source_id": source.id,
                "source_name": source.name,
                "source_url": raw.canonical_url,
                "published_at": raw.published_at,
                "content_form": item.content_form,
            }
        )
        related_by_id.setdefault(
            item.id,
            {
                "message_id": item.id,
                "title": item.translated_title or item.normalized_title,
                "summary": item.summary,
                "source_id": source.id,
                "source_name": source.name,
                "source_url": raw.canonical_url,
                "published_at": raw.published_at,
                "content_form": item.content_form,
            },
        )
        if mention.materiality == "material_update":
            timeline.append(
                {
                    "mention_id": mention.id,
                    "message_id": item.id,
                    "message_revision": mention.normalized_item_revision,
                    "occurred_at": published_at,
                    "relation": mention.relation,
                    "title": item.translated_title or item.normalized_title,
                    "note": mention.evidence_excerpt,
                    "structured_fact_changes": mention.structured_fact_changes,
                    "source_id": source.id,
                    "source_name": source.name,
                }
            )
    return {
        **event_card_payload(db, event),
        "latest_development": event.latest_development,
        "key_facts": event.key_facts,
        "canonical_anchors": event.canonical_anchors,
        "importance_breakdown": event.importance_breakdown,
        "credibility_breakdown": event.credibility_breakdown,
        "heat_breakdown": event.heat_breakdown,
        "timeline": timeline,
        "evidence": evidence,
        "related_messages": list(related_by_id.values()),
        "references": {
            "origin_message_id": event.origin_message_id,
            "primary_source_message_id": event.primary_source_message_id,
            "latest_update_message_id": event.latest_update_message_id,
            "best_media_message_id": event.best_media_message_id,
        },
    }
