from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.event import Event, EventMention
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem


def get_normalized_item(db: Session, normalized_item_id: int) -> NormalizedItem | None:
    return db.scalar(
        select(NormalizedItem)
        .where(NormalizedItem.id == normalized_item_id)
        .options(
            selectinload(NormalizedItem.raw_item).selectinload(RawItem.source),
        )
    )


def get_event_for_update(db: Session, event_id: int) -> Event | None:
    return db.scalar(select(Event).where(Event.id == event_id).with_for_update())


def find_mention(
    db: Session,
    *,
    normalized_item_id: int,
    normalized_item_revision: int,
    mention_index: int,
    aggregation_policy_version: str,
) -> EventMention | None:
    return db.scalar(
        select(EventMention).where(
            EventMention.normalized_item_id == normalized_item_id,
            EventMention.normalized_item_revision == normalized_item_revision,
            EventMention.mention_index == mention_index,
            EventMention.aggregation_policy_version == aggregation_policy_version,
        )
    )


def current_event_mention_conditions():
    """Return the SQL predicates for the current public Event projection."""
    return (
        NormalizedItem.publication_status == "published",
        EventMention.normalized_item_revision == NormalizedItem.current_revision,
    )


def event_ids_for_normalized_item(db: Session, normalized_item_id: int) -> set[int]:
    return set(
        db.scalars(
            select(EventMention.event_id).where(
                EventMention.normalized_item_id == normalized_item_id
            )
        )
    )


def count_active_messages(db: Session, event_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(func.distinct(EventMention.normalized_item_id)))
            .join(EventMention.normalized_item)
            .where(
                EventMention.event_id == event_id,
                *current_event_mention_conditions(),
            )
        )
        or 0
    )
