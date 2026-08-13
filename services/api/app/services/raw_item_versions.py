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
