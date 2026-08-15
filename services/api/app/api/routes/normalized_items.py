from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domain.message_taxonomy import MESSAGE_TYPE_ORDER, PRODUCTS, TOPIC_RULES, Product
from app.models.normalized_item import NormalizedItem
from app.schemas.normalized_item import (
    NormalizedItemRead,
    PublishedDayListRead,
    PublishedItemPageRead,
    PublishedItemRead,
)
from app.services.published_items import (
    DEFAULT_PUBLICATION_TIMEZONE,
    get_published_item as read_published_item,
    list_published_days as read_published_days,
    search_published_items,
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


@router.get("/published", response_model=list[PublishedItemRead])
def list_published_items(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return search_published_items(db, limit=100).items


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
    try:
        result = search_published_items(
            db,
            query=search,
            product=product,
            message_type=message_type,
            featured=featured,
            published_date=published_date,
            timezone_name=timezone_name,
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
    try:
        return read_published_days(
            db,
            product=product,
            message_type=message_type,
            featured=featured,
            search=search,
            timezone_name=timezone_name,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{item_id}/published", response_model=PublishedItemRead)
def get_published_item(
    item_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = read_published_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="published item not found")
    return item
