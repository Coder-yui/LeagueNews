from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domain.event_categories import EVENT_CATEGORIES
from app.domain.event_types import CREDIBILITY_LEVELS, EVENT_FAMILIES, EVENT_LIFECYCLES
from app.domain.message_taxonomy import Product, PRODUCTS
from app.schemas.event import EventDetailRead, EventPageRead
from app.services.event_read import get_event_detail, search_events


router = APIRouter()


@router.get("", response_model=EventPageRead)
def list_events(
    product: Product | None = None,
    category: str | None = None,
    event_family: str | None = None,
    lifecycle: str | None = None,
    credibility_level: str | None = None,
    importance_level: str | None = None,
    heat_level: str | None = None,
    search: str | None = None,
    sort_by: str = Query(default="time", pattern="^(time|latest|importance|heat)$"),
    sort: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        result = search_events(
            db,
            product=product,
            category=category,
            event_family=event_family,
            lifecycle=lifecycle,
            credibility=credibility_level,
            importance=importance_level,
            heat=heat_level,
            query=search,
            sort_by=sort_by,
            sort=sort,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "items": result.items,
        "total": result.total,
        "product_options": list(PRODUCTS),
        "event_family_options": sorted(EVENT_FAMILIES),
        "lifecycle_options": sorted(EVENT_LIFECYCLES),
        "credibility_options": sorted(CREDIBILITY_LEVELS),
        "category_options": list(EVENT_CATEGORIES),
    }


@router.get("/{event_id}", response_model=EventDetailRead)
def get_event(event_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    event = get_event_detail(db, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return event
