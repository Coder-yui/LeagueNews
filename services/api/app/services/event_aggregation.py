from math import prod
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.event import Event, EventMessage, EventRevision
from app.models.normalized_item import NormalizedItem
from app.services.raw_item_versions import superseded_normalized_item_ids


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


def _superseded_memberships(
    db: Session,
    item: NormalizedItem,
) -> list[EventMessage]:
    normalized_item_ids = superseded_normalized_item_ids(db, item)
    if not normalized_item_ids:
        return []
    return list(
        db.scalars(
            select(EventMessage).where(
                EventMessage.normalized_item_id.in_(normalized_item_ids)
            )
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


def _default_independence_key(item: NormalizedItem) -> str:
    return f"source:{item.raw_item.source_id}"


def _refresh_editorial_metrics(db: Session, event: Event) -> None:
    memberships = list(
        db.scalars(
            select(EventMessage)
            .where(EventMessage.event_id == event.id)
        )
    )
    if not memberships:
        event.credibility_status = "unverified"
        event.credibility_score = 0
        event.independent_source_count = 0
        event.official_source_count = 0
        return

    items = {
        item.id: item
        for item in db.scalars(
            select(NormalizedItem).where(
                NormalizedItem.id.in_(
                    membership.normalized_item_id for membership in memberships
                )
            )
        )
    }
    supporting: dict[str, float] = {}
    contradicting: dict[str, float] = {}
    official_support = set()
    official_contradiction = set()
    all_sources = set()

    for membership in memberships:
        item = items[membership.normalized_item_id]
        key = membership.independence_key or _default_independence_key(item)
        all_sources.add(key)
        if membership.evidence_stance == "context":
            continue
        if membership.is_official_confirmation:
            if membership.evidence_stance == "contradicts":
                official_contradiction.add(key)
            else:
                official_support.add(key)
            continue
        strength = min(0.85, max(0.0, item.credibility_score))
        target = (
            contradicting
            if membership.evidence_stance == "contradicts"
            else supporting
        )
        target[key] = max(target.get(key, 0), strength)

    event.independent_source_count = len(all_sources)
    event.official_source_count = len(official_support | official_contradiction)

    positive = 1 - prod(1 - strength for strength in supporting.values())
    negative = 1 - prod(1 - strength for strength in contradicting.values())
    if official_contradiction and official_support:
        event.credibility_status = "disputed"
        event.credibility_score = 0.5
        event.lifecycle_status = "disputed"
    elif official_contradiction:
        event.credibility_status = "officially_refuted"
        event.credibility_score = 0
        event.lifecycle_status = "officially_refuted"
    elif official_support:
        event.credibility_status = "official_confirmed"
        event.credibility_score = 1
        if event.lifecycle_status in {"developing", "unconfirmed", "disputed"}:
            event.lifecycle_status = "confirmed"
    else:
        event.credibility_score = round(positive * (1 - negative), 6)
        if positive and negative >= 0.25:
            event.credibility_status = "disputed"
            event.lifecycle_status = "disputed"
        elif len(supporting) >= 2:
            event.credibility_status = "multi_source_confirmed"
        elif len(supporting) == 1:
            event.credibility_status = "single_source"
        else:
            event.credibility_status = "unverified"



def create_event(
    db: Session,
    *,
    normalized_item_id: int,
    title: str,
    summary: str,
    category: str,
    event_key: str | None = None,
    status: str = "active",
    event_type: str = "other",
    lifecycle_status: str = "developing",
    evidence_stance: str = "supports",
    independence_key: str | None = None,
    is_official_confirmation: bool | None = None,
    importance_score: float | None = None,
    importance_evidence: list[str] | None = None,
    latest_development: str | None = None,
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
    superseded_memberships = _superseded_memberships(db, item)
    if superseded_memberships:
        raise EventMembershipConflictError(
            f"an earlier raw revision already belongs to event "
            f"{superseded_memberships[0].event_id}; update that event instead"
        )

    try:
        official_confirmation = (
            item.credibility == "official"
            if is_official_confirmation is None
            else is_official_confirmation
        )
        event = Event(
            event_key=event_key,
            title=title,
            summary=summary,
            category=category,
            status=status,
            event_type=event_type,
            lifecycle_status=lifecycle_status,
            importance_score=(
                item.importance_score
                if importance_score is None
                else importance_score
            ),
            importance_evidence=importance_evidence or [
                f"由首条成员消息的重要性 {item.importance_score:.0%} 初始化"
            ],
            latest_development=latest_development or change_note,
            current_revision=1,
        )
        db.add(event)
        db.flush()
        db.add(
            EventMessage(
                event_id=event.id,
                normalized_item_id=item.id,
                relation_type="primary",
                evidence_stance=evidence_stance,
                independence_key=independence_key or _default_independence_key(item),
                is_official_confirmation=official_confirmation,
                is_significant_update=True,
                source_published_at=item.raw_item.published_at,
            )
        )
        db.flush()
        _refresh_publish_range(db, event)
        _refresh_editorial_metrics(db, event)
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
    lifecycle_status: str | None = None,
    evidence_stance: str = "supports",
    independence_key: str | None = None,
    is_official_confirmation: bool | None = None,
    is_significant_update: bool = True,
    importance_score: float | None = None,
    importance_evidence: list[str] | None = None,
    latest_development: str | None = None,
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
    replaced_memberships = _superseded_memberships(db, item)
    conflicting_membership = next(
        (
            predecessor
            for predecessor in replaced_memberships
            if predecessor.event_id != event_id
        ),
        None,
    )
    if conflicting_membership is not None:
        raise EventMembershipConflictError(
            f"superseded normalized item {conflicting_membership.normalized_item_id} "
            f"belongs to event {conflicting_membership.event_id}"
        )
    event = db.scalar(
        select(Event).where(Event.id == event_id).with_for_update()
    )
    if event is None:
        raise EventNotFoundError(f"event {event_id} not found")

    try:
        replaced_normalized_item_ids = [
            predecessor.normalized_item_id
            for predecessor in replaced_memberships
        ]
        for predecessor in replaced_memberships:
            db.delete(predecessor)
        if replaced_memberships:
            db.flush()
        official_confirmation = (
            item.credibility == "official"
            if is_official_confirmation is None
            else is_official_confirmation
        )
        db.add(
            EventMessage(
                event_id=event.id,
                normalized_item_id=item.id,
                relation_type="primary",
                evidence_stance=evidence_stance,
                independence_key=independence_key or _default_independence_key(item),
                is_official_confirmation=official_confirmation,
                is_significant_update=is_significant_update,
                source_published_at=item.raw_item.published_at,
            )
        )
        if is_significant_update:
            event.current_revision += 1
            if title is not None:
                event.title = title
            if summary is not None:
                event.summary = summary
            if lifecycle_status is not None:
                event.lifecycle_status = lifecycle_status
            event.latest_development = latest_development or change_note
        if importance_score is not None:
            event.importance_score = max(event.importance_score, importance_score)
        if importance_evidence:
            event.importance_evidence = importance_evidence
        db.flush()
        _refresh_publish_range(db, event)
        _refresh_editorial_metrics(db, event)
        if is_significant_update:
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
                        evidence={
                            **(evidence or {}),
                            "replaces_normalized_item_ids": (
                                replaced_normalized_item_ids
                            ),
                        },
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
