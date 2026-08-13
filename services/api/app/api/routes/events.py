from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Text, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domain.event_types import (
    CREDIBILITY_LEVELS,
    EVENT_FAMILIES,
    EVENT_LIFECYCLES,
)
from app.domain.event_categories import EVENT_CATEGORIES
from app.domain.message_taxonomy import PRODUCTS
from app.models.event import Event
from app.schemas.event import EventDetailRead, EventPageRead
from app.services.event_presentation import (
    event_card_payload,
    event_detail_payload,
    event_message_counts,
    event_reference_items,
)


router = APIRouter()


_ESPORTS_FAMILIES = ("esports_match", "esports_schedule", "roster_change", "esports_rules")
_ECOSYSTEM_FAMILIES = ("corporate_change", "platform_service", "universe_release", "media_release")


def _contains_product(product: str):
    return cast(Event.products, Text).like(f'%"{product}"%')


def _category_condition(category: str):
    if category == "esports":
        return or_(Event.event_family.in_(_ESPORTS_FAMILIES), _contains_product("lol_esports"))
    if category == "ecosystem":
        return or_(
            Event.event_family.in_(_ECOSYSTEM_FAMILIES),
            _contains_product("riot_ecosystem"),
            _contains_product("lol_universe"),
        )
    excluded = and_(
        Event.event_family.not_in((*_ESPORTS_FAMILIES, *_ECOSYSTEM_FAMILIES)),
        ~_contains_product("lol_esports"),
        ~_contains_product("riot_ecosystem"),
        ~_contains_product("lol_universe"),
    )
    if category == "lol_pc":
        return and_(excluded, _contains_product("lol_pc"))
    if category == "tft":
        return and_(excluded, ~_contains_product("lol_pc"), _contains_product("tft"))
    return and_(
        excluded,
        ~_contains_product("lol_pc"),
        ~_contains_product("tft"),
    )


@router.get("", response_model=EventPageRead)
def list_events(
    product: str | None = None,
    category: str | None = None,
    event_family: str | None = None,
    lifecycle: str | None = None,
    credibility_level: str | None = None,
    importance_level: str | None = None,
    heat_level: str | None = None,
    search: str | None = None,
    sort_by: str = Query(default="latest", pattern="^(latest|importance|heat)$"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    conditions = []
    if event_family:
        conditions.append(Event.event_family == event_family)
    if lifecycle:
        conditions.append(Event.lifecycle_status == lifecycle)
    if credibility_level:
        conditions.append(Event.credibility_level == credibility_level)
    if product:
        conditions.append(_contains_product(product))
    if category:
        if category not in EVENT_CATEGORIES:
            raise HTTPException(status_code=400, detail="unsupported event category")
        conditions.append(_category_condition(category))
    if importance_level:
        ranges = {
            "low": Event.importance_score < 0.30,
            "medium": and_(Event.importance_score >= 0.30, Event.importance_score < 0.55),
            "high": and_(Event.importance_score >= 0.55, Event.importance_score < 0.80),
            "critical": Event.importance_score >= 0.80,
        }
        if importance_level not in ranges:
            raise HTTPException(status_code=400, detail="unsupported importance level")
        conditions.append(ranges[importance_level])
    if heat_level:
        ranges = {
            "cold": Event.heat_score < 0.15,
            "emerging": and_(Event.heat_score >= 0.15, Event.heat_score < 0.35),
            "active": and_(Event.heat_score >= 0.35, Event.heat_score < 0.60),
            "hot": and_(Event.heat_score >= 0.60, Event.heat_score < 0.80),
            "surging": Event.heat_score >= 0.80,
        }
        if heat_level not in ranges:
            raise HTTPException(status_code=400, detail="unsupported heat level")
        conditions.append(ranges[heat_level])
    if search:
        needle = search.strip().casefold()
        if needle:
            pattern = f"%{needle}%"
            conditions.append(
                or_(Event.title.ilike(pattern), Event.current_summary.ilike(pattern))
            )
    total = db.scalar(select(func.count(Event.id)).where(*conditions)) or 0
    ordering = {
        "latest": (Event.last_material_update_at.desc().nullslast(), Event.id.desc()),
        "importance": (
            Event.importance_score.desc(),
            Event.last_material_update_at.desc().nullslast(),
            Event.id.desc(),
        ),
        "heat": (
            Event.heat_score.desc(),
            Event.last_material_update_at.desc().nullslast(),
            Event.id.desc(),
        ),
    }[sort_by]
    events = list(
        db.scalars(
            select(Event).where(*conditions).order_by(*ordering).offset(offset).limit(limit)
        )
    )
    counts = event_message_counts(db, [event.id for event in events])
    reference_items = event_reference_items(db, events)
    payloads = [
        event_card_payload(
            db,
            event,
            counts=counts.get(event.id, (0, 0)),
            reference_items=reference_items,
        )
        for event in events
    ]
    return {
        "items": payloads,
        "total": total,
        "product_options": list(PRODUCTS),
        "event_family_options": sorted(EVENT_FAMILIES),
        "lifecycle_options": sorted(EVENT_LIFECYCLES),
        "credibility_options": sorted(CREDIBILITY_LEVELS),
        "category_options": list(EVENT_CATEGORIES),
    }


@router.get("/{event_id}", response_model=EventDetailRead)
def get_event(event_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return event_detail_payload(db, event)
