from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased

from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem


def latest_raw_item_condition():
    successor = aliased(RawItem)
    return ~exists(
        select(successor.id).where(
            successor.supersedes_raw_item_id == RawItem.id
        )
    )


def is_latest_raw_item(db: Session, item: RawItem) -> bool:
    return not bool(
        db.scalar(
            select(RawItem.id)
            .where(RawItem.supersedes_raw_item_id == item.id)
            .limit(1)
        )
    )


def latest_normalized_item_condition():
    successor = aliased(RawItem)
    return ~exists(
        select(successor.id).where(
            successor.supersedes_raw_item_id == NormalizedItem.raw_item_id
        )
    )


def is_latest_normalized_item(db: Session, item: NormalizedItem) -> bool:
    return not bool(
        db.scalar(
            select(RawItem.id)
            .where(RawItem.supersedes_raw_item_id == item.raw_item_id)
            .limit(1)
        )
    )


def superseded_normalized_item_ids(
    db: Session,
    item: NormalizedItem,
) -> list[int]:
    raw_ids: list[int] = []
    raw_id = item.raw_item.supersedes_raw_item_id
    while raw_id is not None:
        raw_ids.append(raw_id)
        raw_id = db.scalar(
            select(RawItem.supersedes_raw_item_id).where(RawItem.id == raw_id)
        )
    if not raw_ids:
        return []
    return list(
        db.scalars(
            select(NormalizedItem.id).where(
                NormalizedItem.raw_item_id.in_(raw_ids)
            )
        )
    )
