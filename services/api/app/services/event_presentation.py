from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.event_heat import heat_level
from app.domain.event_importance import importance_level
from app.models.event import Event, EventMention
from app.models.normalized_item import NormalizedItem
from app.services.event_metrics import refresh_event_metrics


def refresh_stale_event_metrics(
    db: Session,
    events: list[Event],
    *,
    as_of: datetime | None = None,
    ttl: timedelta = timedelta(minutes=5),
) -> None:
    reference = as_of or datetime.now(UTC)
    stale_ids = {
        event.id
        for event in events
        if event.heat_calculated_at is None
        or reference
        - (
            event.heat_calculated_at.replace(tzinfo=UTC)
            if event.heat_calculated_at.tzinfo is None
            else event.heat_calculated_at.astimezone(UTC)
        )
        >= ttl
    }
    if stale_ids:
        refresh_event_metrics(db, stale_ids, as_of=reference)
        db.commit()
        for event in events:
            if event.id in stale_ids:
                db.refresh(event)


def _source_payload(db: Session, message_id: int | None) -> dict[str, Any] | None:
    item = db.get(NormalizedItem, message_id) if message_id is not None else None
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


def _best_media_url(db: Session, message_id: int | None) -> str | None:
    item = db.get(NormalizedItem, message_id) if message_id is not None else None
    if item is None:
        return None
    assets = sorted(item.raw_item.media_assets, key=lambda value: (value.block_index, value.id))
    return next((asset.public_path for asset in assets if asset.public_path), None)


def event_card_payload(db: Session, event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "title": event.title,
        "current_summary": event.current_summary,
        "products": event.products,
        "event_family": event.event_family,
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
        "primary_source": _source_payload(db, event.primary_source_message_id),
        "best_media_url": _best_media_url(db, event.best_media_message_id),
    }


def event_detail_payload(db: Session, event: Event) -> dict[str, Any]:
    mentions = list(
        db.scalars(
            select(EventMention)
            .join(EventMention.normalized_item)
            .where(
                EventMention.event_id == event.id,
                NormalizedItem.publication_status == "published",
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
        "unresolved_points": event.unresolved_points,
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
