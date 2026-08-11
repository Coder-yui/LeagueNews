from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domain.event_types import (
    CREDIBILITY_LEVELS,
    EVENT_FAMILIES,
    EVENT_LIFECYCLES,
)
from app.domain.message_taxonomy import PRODUCTS
from app.models.event import Event
from app.schemas.event import EventDetailRead, EventPageRead
from app.services.event_presentation import (
    event_card_payload,
    event_detail_payload,
    refresh_stale_event_metrics,
)


router = APIRouter()


def _time_key(value: datetime | None) -> float:
    if value is None:
        return 0
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.timestamp()


@router.get("", response_model=EventPageRead)
def list_events(
    product: str | None = None,
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
    events = list(db.scalars(select(Event).where(*conditions)))
    refresh_stale_event_metrics(db, events)
    payloads = [event_card_payload(db, event) for event in events]
    if product:
        payloads = [payload for payload in payloads if product in payload["products"]]
    if importance_level:
        payloads = [
            payload for payload in payloads if payload["importance_level"] == importance_level
        ]
    if heat_level:
        payloads = [payload for payload in payloads if payload["heat_level"] == heat_level]
    if search:
        needle = search.strip().casefold()
        payloads = [
            payload
            for payload in payloads
            if needle in str(payload["title"]).casefold()
            or needle in str(payload["current_summary"]).casefold()
        ]
    sort_key = {
        "latest": lambda payload: (
            _time_key(payload["last_material_update_at"]),
            int(payload["id"]),
        ),
        "importance": lambda payload: (
            float(payload["importance_score"]),
            _time_key(payload["last_material_update_at"]),
            int(payload["id"]),
        ),
        "heat": lambda payload: (
            float(payload["heat_score"]),
            _time_key(payload["last_material_update_at"]),
            int(payload["id"]),
        ),
    }[sort_by]
    payloads.sort(key=sort_key, reverse=True)
    total = len(payloads)
    return {
        "items": payloads[offset : offset + limit],
        "total": total,
        "product_options": list(PRODUCTS),
        "event_family_options": sorted(EVENT_FAMILIES),
        "lifecycle_options": sorted(EVENT_LIFECYCLES),
        "credibility_options": sorted(CREDIBILITY_LEVELS),
    }


@router.get("/{event_id}", response_model=EventDetailRead)
def get_event(event_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    refresh_stale_event_metrics(db, [event])
    return event_detail_payload(db, event)
