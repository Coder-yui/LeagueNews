from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.query_filters import json_array_contains
from app.domain.event_categories import EVENT_CATEGORIES
from app.domain.message_taxonomy import Product
from app.models.event import Event, EventMention
from app.models.normalized_item import NormalizedItem
from app.repositories.events import current_event_mention_conditions
from app.services.event_presentation import (
    event_card_payload,
    event_detail_payload,
    event_message_counts,
    event_reference_items,
)


_ESPORTS_FAMILIES = ("esports_match", "esports_schedule", "roster_change", "esports_rules")
_ECOSYSTEM_FAMILIES = (
    "corporate_change",
    "platform_service",
    "universe_release",
    "media_release",
)


@dataclass(frozen=True, slots=True)
class EventSearchResult:
    items: list[dict[str, Any]]
    total: int


def _contains_product(db: Session, product: str):
    return json_array_contains(db, Event.products, product)


def _category_condition(db: Session, category: str):
    if category == "esports":
        return or_(
            Event.event_family.in_(_ESPORTS_FAMILIES),
            _contains_product(db, "lol_esports"),
        )
    if category == "ecosystem":
        return or_(
            Event.event_family.in_(_ECOSYSTEM_FAMILIES),
            _contains_product(db, "riot_ecosystem"),
            _contains_product(db, "lol_universe"),
        )
    excluded = and_(
        Event.event_family.not_in((*_ESPORTS_FAMILIES, *_ECOSYSTEM_FAMILIES)),
        ~_contains_product(db, "lol_esports"),
        ~_contains_product(db, "riot_ecosystem"),
        ~_contains_product(db, "lol_universe"),
    )
    if category == "lol_pc":
        return and_(excluded, _contains_product(db, "lol_pc"))
    if category == "tft":
        return and_(
            excluded,
            ~_contains_product(db, "lol_pc"),
            _contains_product(db, "tft"),
        )
    return and_(
        excluded,
        ~_contains_product(db, "lol_pc"),
        ~_contains_product(db, "tft"),
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def event_conditions(
    db: Session,
    *,
    product: Product | None = None,
    category: str | None = None,
    event_family: str | None = None,
    lifecycle: str | None = None,
    credibility: str | None = None,
    importance: str | None = None,
    heat: str | None = None,
    query: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Any]:
    conditions: list[Any] = [
        select(EventMention.id)
        .join(EventMention.normalized_item)
        .where(
            EventMention.event_id == Event.id,
            *current_event_mention_conditions(),
        )
        .exists()
    ]
    if event_family:
        conditions.append(Event.event_family == event_family)
    if lifecycle:
        conditions.append(Event.lifecycle_status == lifecycle)
    if credibility:
        conditions.append(Event.credibility_level == credibility)
    if product:
        conditions.append(_contains_product(db, product))
    if category:
        if category not in EVENT_CATEGORIES:
            raise ValueError("unsupported event category")
        conditions.append(_category_condition(db, category))
    if importance:
        ranges = {
            "low": Event.importance_score < 0.30,
            "medium": and_(Event.importance_score >= 0.30, Event.importance_score < 0.55),
            "high": and_(Event.importance_score >= 0.55, Event.importance_score < 0.80),
            "critical": Event.importance_score >= 0.80,
        }
        if importance not in ranges:
            raise ValueError("unsupported importance level")
        conditions.append(ranges[importance])
    if heat:
        ranges = {
            "cold": Event.heat_score < 0.15,
            "emerging": and_(Event.heat_score >= 0.15, Event.heat_score < 0.35),
            "active": and_(Event.heat_score >= 0.35, Event.heat_score < 0.60),
            "hot": and_(Event.heat_score >= 0.60, Event.heat_score < 0.80),
            "surging": Event.heat_score >= 0.80,
        }
        if heat not in ranges:
            raise ValueError("unsupported heat level")
        conditions.append(ranges[heat])
    if query:
        needle = query.strip().casefold()
        if needle:
            pattern = f"%{needle}%"
            conditions.append(or_(Event.title.ilike(pattern), Event.current_summary.ilike(pattern)))
    event_time = func.coalesce(Event.last_material_update_at, Event.last_seen_at, Event.created_at)
    if since is not None:
        conditions.append(event_time >= _as_utc(since))
    if until is not None:
        conditions.append(event_time < _as_utc(until))
    return conditions


def search_events(
    db: Session,
    *,
    query: str | None = None,
    product: Product | None = None,
    category: str | None = None,
    event_family: str | None = None,
    lifecycle: str | None = None,
    credibility: str | None = None,
    importance: str | None = None,
    heat: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    sort_by: str = "time",
    sort: str = "desc",
    limit: int = 25,
    offset: int = 0,
) -> EventSearchResult:
    conditions = event_conditions(
        db,
        product=product,
        category=category,
        event_family=event_family,
        lifecycle=lifecycle,
        credibility=credibility,
        importance=importance,
        heat=heat,
        query=query,
        since=since,
        until=until,
    )
    total = db.scalar(select(func.count(Event.id)).where(*conditions)) or 0
    time_column = func.coalesce(Event.last_material_update_at, Event.last_seen_at, Event.created_at)
    sort_column = {
        "time": time_column,
        "latest": time_column,
        "importance": Event.importance_score,
        "heat": Event.heat_score,
    }.get(sort_by)
    if sort_column is None:
        raise ValueError("unsupported event sort")
    ordering = (
        (sort_column.asc(), time_column.asc(), Event.id.asc())
        if sort == "asc"
        else (sort_column.desc(), time_column.desc(), Event.id.desc())
    )
    events = list(
        db.scalars(select(Event).where(*conditions).order_by(*ordering).offset(offset).limit(limit))
    )
    counts = event_message_counts(db, [event.id for event in events])
    reference_items = event_reference_items(db, events)
    return EventSearchResult(
        items=[
            event_card_payload(
                db,
                event,
                counts=counts.get(event.id, (0, 0)),
                reference_items=reference_items,
            )
            for event in events
        ],
        total=int(total),
    )


def get_event_detail(db: Session, event_id: int) -> dict[str, Any] | None:
    event = db.scalar(
        select(Event).where(
            Event.id == event_id,
            *event_conditions(db),
        )
    )
    return event_detail_payload(db, event) if event is not None else None


def event_associations_for_messages(
    db: Session,
    message_ids: set[int] | list[int],
) -> dict[int, list[dict[str, Any]]]:
    """Return only events with a current, published mention for each message."""
    if not message_ids:
        return {}
    rows = db.execute(
        select(EventMention.normalized_item_id, Event)
        .join(Event, Event.id == EventMention.event_id)
        .join(NormalizedItem, NormalizedItem.id == EventMention.normalized_item_id)
        .where(
            EventMention.normalized_item_id.in_(message_ids),
            *current_event_mention_conditions(),
        )
        .order_by(EventMention.normalized_item_id, Event.id)
    )
    result: dict[int, list[dict[str, Any]]] = {}
    seen: set[tuple[int, int]] = set()
    for message_id, event in rows:
        key = (message_id, event.id)
        if key in seen:
            continue
        seen.add(key)
        result.setdefault(message_id, []).append(
            {
                "id": event.id,
                "title": event.title,
                "summary": event.current_summary,
                "products": event.products,
                "event_family": event.event_family,
                "lifecycle": event.lifecycle_status,
                "importance_score": event.importance_score,
                "credibility": event.credibility_level,
                "credibility_score": event.credibility_score,
                "heat": event.heat_score,
                "heat_level": str(event.heat_breakdown.get("level") or ""),
            }
        )
    return result
