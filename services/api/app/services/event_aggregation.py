from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.event import Event, EventMessage, EventRevision
from app.models.normalized_item import NormalizedItem


class EventAggregationError(RuntimeError):
    pass


class EventNotFoundError(EventAggregationError):
    pass


class EventMembershipConflictError(EventAggregationError):
    pass


def _get_normalized_item(db: Session, normalized_item_id: int) -> NormalizedItem:
    item = db.get(NormalizedItem, normalized_item_id)
    if item is None:
        raise EventNotFoundError(f"normalized item {normalized_item_id} not found")
    return item


def _membership_snapshot(
    *,
    action: str,
    normalized_item_id: int,
    evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "action": action,
        "normalized_item_id": normalized_item_id,
        "evidence": evidence or {},
    }


def _existing_membership(db: Session, normalized_item_id: int) -> EventMessage | None:
    return db.scalar(
        select(EventMessage).where(
            EventMessage.normalized_item_id == normalized_item_id
        )
    )


def _refresh_publish_range(db: Session, event: Event) -> None:
    first_published_at, last_published_at = db.execute(
        select(
            func.min(EventMessage.source_published_at),
            func.max(EventMessage.source_published_at),
        ).where(EventMessage.event_id == event.id)
    ).one()
    event.first_published_at = first_published_at
    event.last_published_at = last_published_at


def create_event(
    db: Session,
    *,
    normalized_item_id: int,
    title: str,
    summary: str,
    category: str,
    event_key: str | None = None,
    status: str = "active",
    change_note: str = "创建事件",
    evidence: dict[str, Any] | None = None,
    commit: bool = True,
) -> Event:
    item = _get_normalized_item(db, normalized_item_id)
    membership = _existing_membership(db, normalized_item_id)
    if membership is not None:
        raise EventMembershipConflictError(
            f"normalized item {normalized_item_id} already belongs to event "
            f"{membership.event_id}"
        )

    try:
        event = Event(
            event_key=event_key,
            title=title,
            summary=summary,
            category=category,
            status=status,
            current_revision=1,
        )
        db.add(event)
        db.flush()
        db.add(
            EventMessage(
                event_id=event.id,
                normalized_item_id=item.id,
                relation_type="primary",
                source_published_at=item.raw_item.published_at,
            )
        )
        db.flush()
        _refresh_publish_range(db, event)
        db.add(
            EventRevision(
                event_id=event.id,
                revision=1,
                title=title,
                summary=summary,
                change_note=change_note,
                evidence_snapshot=_membership_snapshot(
                    action="create",
                    normalized_item_id=normalized_item_id,
                    evidence=evidence,
                ),
            )
        )
        if commit:
            db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise EventMembershipConflictError(
            "event key or normalized item membership already exists"
        ) from exc
    if commit:
        db.refresh(event)
    return event


def add_message_to_event(
    db: Session,
    *,
    event_id: int,
    normalized_item_id: int,
    title: str | None = None,
    summary: str | None = None,
    change_note: str = "新增事件消息",
    evidence: dict[str, Any] | None = None,
    commit: bool = True,
) -> tuple[Event, bool]:
    membership = _existing_membership(db, normalized_item_id)
    if membership is not None:
        if membership.event_id != event_id:
            raise EventMembershipConflictError(
                f"normalized item {normalized_item_id} already belongs to event "
                f"{membership.event_id}"
            )
        event = db.get(Event, event_id)
        if event is None:
            raise EventNotFoundError(f"event {event_id} not found")
        return event, False

    item = _get_normalized_item(db, normalized_item_id)
    event = db.scalar(
        select(Event).where(Event.id == event_id).with_for_update()
    )
    if event is None:
        raise EventNotFoundError(f"event {event_id} not found")

    try:
        db.add(
            EventMessage(
                event_id=event.id,
                normalized_item_id=item.id,
                relation_type="primary",
                source_published_at=item.raw_item.published_at,
            )
        )
        event.current_revision += 1
        if title is not None:
            event.title = title
        if summary is not None:
            event.summary = summary
        db.flush()
        _refresh_publish_range(db, event)
        db.add(
            EventRevision(
                event_id=event.id,
                revision=event.current_revision,
                title=event.title,
                summary=event.summary,
                change_note=change_note,
                evidence_snapshot=_membership_snapshot(
                    action="update",
                    normalized_item_id=normalized_item_id,
                    evidence=evidence,
                ),
            )
        )
        if commit:
            db.commit()
    except IntegrityError as exc:
        db.rollback()
        membership = _existing_membership(db, normalized_item_id)
        if membership is not None and membership.event_id == event_id:
            existing_event = db.get(Event, event_id)
            if existing_event is not None:
                return existing_event, False
        raise EventMembershipConflictError(
            "event membership or revision conflicted with another update"
        ) from exc
    if commit:
        db.refresh(event)
    return event, True
