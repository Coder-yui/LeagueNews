from datetime import UTC, datetime
from math import prod
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.importance import TOPIC_RANGES
from app.domain.ontology import TIMELINE_EVENT_TYPES, topic_from_category
from app.models.event import Event, EventMessage, EventRevision
from app.models.normalized_item import NormalizedItem
from app.services.claims import (
    link_item_claims_to_event,
    unlink_item_claims_from_event,
)
from app.services.credibility import record_source_outcome
from app.services.raw_item_versions import superseded_normalized_item_ids

_MATCH_LIFECYCLE_RANK = {
    "scheduled": 0,
    "live": 1,
    "completed": 2,
}
RUMOR_EXPIRY_DAYS = 14
RUMOR_DECAY_HALF_LIFE_DAYS = 14


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


def _existing_membership(
    db: Session,
    normalized_item_id: int,
    event_id: int,
) -> EventMessage | None:
    return db.scalar(
        select(EventMessage).where(
            EventMessage.normalized_item_id == normalized_item_id,
            EventMessage.event_id == event_id,
            EventMessage.membership_status == "active",
        )
    )


def _superseded_memberships(
    db: Session,
    item: NormalizedItem,
    event_id: int,
) -> list[EventMessage]:
    normalized_item_ids = superseded_normalized_item_ids(db, item)
    if not normalized_item_ids:
        return []
    return list(
        db.scalars(
            select(EventMessage).where(
                EventMessage.normalized_item_id.in_(normalized_item_ids),
                EventMessage.event_id == event_id,
                EventMessage.membership_status == "active",
            )
        )
    )


def _refresh_publish_range(db: Session, event: Event) -> None:
    first_published_at, last_published_at = db.execute(
        select(
            func.min(EventMessage.source_published_at),
            func.max(EventMessage.source_published_at),
        ).where(
            EventMessage.event_id == event.id,
            EventMessage.membership_status == "active",
        )
    ).one()
    event.first_published_at = first_published_at
    event.last_published_at = last_published_at


def _default_independence_key(item: NormalizedItem) -> str:
    for block in item.raw_item.content_blocks:
        if block.get("embed_kind") != "quoted_post" or not block.get("source_url"):
            continue
        parsed = urlsplit(str(block["source_url"]))
        if parsed.hostname:
            return f"upstream:{parsed.hostname.casefold()}{parsed.path.rstrip('/')}"
    return f"source:{item.raw_item.source_id}"


def _refresh_editorial_metrics(db: Session, event: Event) -> None:
    previous_credibility_status = event.credibility_status
    memberships = list(
        db.scalars(
            select(EventMessage)
            .where(
                EventMessage.event_id == event.id,
                EventMessage.membership_status == "active",
            )
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
        strength = max(0.0, min(1.0, item.credibility_score))
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
        if previous_credibility_status != "officially_refuted":
            _calibrate_resolved_sources(
                db,
                memberships=memberships,
                items=items,
                official_supports=False,
            )
        event.credibility_status = "officially_refuted"
        event.credibility_score = 0
        event.lifecycle_status = "officially_refuted"
    elif official_support:
        if previous_credibility_status != "official_confirmed":
            _calibrate_resolved_sources(
                db,
                memberships=memberships,
                items=items,
                official_supports=True,
            )
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


def refresh_event_metrics(db: Session, event: Event) -> None:
    """Refresh public event projections after an explicit membership change."""
    _refresh_publish_range(db, event)
    _refresh_editorial_metrics(db, event)


def _calibrate_resolved_sources(
    db: Session,
    *,
    memberships: list[EventMessage],
    items: dict[int, NormalizedItem],
    official_supports: bool,
) -> None:
    outcomes_by_source: dict[int, bool] = {}
    sources = {}
    for membership in memberships:
        if (
            membership.is_official_confirmation
            or membership.evidence_stance == "context"
        ):
            continue
        item = items[membership.normalized_item_id]
        source = item.raw_item.source
        sources[source.id] = source
        supports = membership.evidence_stance != "contradicts"
        outcomes_by_source[source.id] = supports == official_supports
    for source_id, was_confirmed in outcomes_by_source.items():
        record_source_outcome(
            db,
            source=sources[source_id],
            was_confirmed=was_confirmed,
        )


def _refresh_event_importance(db: Session, event: Event) -> None:
    memberships = list(
        db.scalars(
            select(EventMessage).where(
                EventMessage.event_id == event.id,
                EventMessage.membership_status == "active",
            )
        )
    )
    if not memberships:
        event.importance_score = 0
        event.importance_evidence = []
        return
    items = {
        item.id: item
        for item in db.scalars(
            select(NormalizedItem).where(
                NormalizedItem.id.in_(
                    membership.normalized_item_id
                    for membership in memberships
                )
            )
        )
    }
    if event.event_type in TIMELINE_EVENT_TYPES:
        significant = [
            membership
            for membership in memberships
            if membership.is_significant_update
        ] or memberships
        latest = max(
            significant,
            key=lambda membership: (
                membership.source_published_at or membership.added_at,
                membership.normalized_item_id,
            ),
        )
        base_item = items[latest.normalized_item_id]
        base = base_item.importance_score
    else:
        base_item = max(
            items.values(),
            key=lambda item: (item.importance_score, item.id),
        )
        base = base_item.importance_score
    confirmation_boost = 1.3 if event.official_source_count else 1.0
    corroboration_boost = 1 + 0.05 * min(
        max(event.independent_source_count - 1, 0),
        4,
    )
    topic = base_item.primary_topic or topic_from_category(event.category)
    topic_cap = (
        0.8
        if topic == "roster"
        else TOPIC_RANGES.get(topic, TOPIC_RANGES["other"])[1]
    )
    event.importance_score = round(
        min(
            topic_cap,
            base * confirmation_boost * corroboration_boost,
        ),
        6,
    )
    event.importance_evidence = [
        f"base={base:.3f}（成员消息 {base_item.id}）",
        f"confirmation_boost={confirmation_boost:.2f}",
        f"corroboration_boost={corroboration_boost:.2f}",
        f"topic_cap={topic_cap:.2f}",
    ]


def expire_stale_unconfirmed_events(
    db: Session,
    *,
    as_of: datetime | None = None,
    expiry_days: int = RUMOR_EXPIRY_DAYS,
    half_life_days: int = RUMOR_DECAY_HALF_LIFE_DAYS,
    commit: bool = True,
) -> list[int]:
    """Expire unconfirmed timelines and deterministically decay credibility."""
    if expiry_days < 1:
        raise ValueError("expiry_days must be positive")
    if half_life_days < 1:
        raise ValueError("half_life_days must be positive")
    reference = as_of or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    else:
        reference = reference.astimezone(UTC)

    affected_ids: list[int] = []
    events = list(
        db.scalars(
            select(Event).where(
                Event.status == "active",
                Event.event_type.in_(TIMELINE_EVENT_TYPES),
                Event.lifecycle_status.in_(
                    ["unconfirmed", "expired_unconfirmed"]
                ),
                Event.official_source_count == 0,
            )
        )
    )
    for event in events:
        latest = event.last_published_at or event.created_at
        if latest is None:
            continue
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        else:
            latest = latest.astimezone(UTC)
        age_days = max(0.0, (reference - latest).total_seconds() / 86_400)
        overdue_days = age_days - expiry_days
        if overdue_days <= 0:
            continue

        was_expired = event.lifecycle_status == "expired_unconfirmed"
        _refresh_editorial_metrics(db, event)
        if event.official_source_count:
            continue
        decay_factor = 0.5 ** (overdue_days / half_life_days)
        event.credibility_score = round(
            event.credibility_score * decay_factor,
            6,
        )
        event.credibility_status = "expired_unconfirmed"
        event.lifecycle_status = "expired_unconfirmed"
        event.latest_development = (
            f"超过 {expiry_days} 天无官方确认或新证据，传闻已过期"
        )
        if not was_expired:
            event.current_revision += 1
            db.add(
                EventRevision(
                    event_id=event.id,
                    revision=event.current_revision,
                    title=event.title,
                    summary=event.summary,
                    change_note="传闻超时：expired_unconfirmed",
                    evidence_snapshot={
                        "action": "expire_unconfirmed",
                        "as_of": reference.isoformat(),
                        "age_days": round(age_days, 4),
                        "expiry_days": expiry_days,
                        "half_life_days": half_life_days,
                        "decay_factor": round(decay_factor, 6),
                    },
                )
            )
        affected_ids.append(event.id)

    if commit and affected_ids:
        db.commit()
    return affected_ids


def refresh_event_projection(db: Session, event: Event) -> None:
    """Recompute derived fields after an event membership is withdrawn or restored."""
    _refresh_publish_range(db, event)
    _refresh_editorial_metrics(db, event)
    _refresh_event_importance(db, event)
    active_count = db.scalar(
        select(func.count(EventMessage.normalized_item_id)).where(
            EventMessage.event_id == event.id,
            EventMessage.membership_status == "active",
        )
    )
    event.status = "active" if active_count else "withdrawn"



def create_event(
    db: Session,
    *,
    normalized_item_id: int,
    title: str,
    summary: str,
    category: str,
    event_key: str | None = None,
    aggregation_key: str | None = None,
    status: str = "active",
    event_type: str = "other",
    lifecycle_status: str = "developing",
    membership_role: str = "primary",
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

    try:
        official_confirmation = (
            item.credibility == "official"
            if is_official_confirmation is None
            else is_official_confirmation
        )
        event = Event(
            event_key=event_key,
            aggregation_key=aggregation_key,
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
                membership_role=membership_role,
                evidence_stance=evidence_stance,
                independence_key=independence_key or _default_independence_key(item),
                is_official_confirmation=official_confirmation,
                is_significant_update=True,
                source_published_at=item.raw_item.published_at,
            )
        )
        db.flush()
        link_item_claims_to_event(
            db,
            normalized_item_id=item.id,
            event_id=event.id,
            relation=evidence_stance,
        )
        _refresh_publish_range(db, event)
        _refresh_editorial_metrics(db, event)
        _refresh_event_importance(db, event)
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
    membership_role: str = "primary",
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
    membership = _existing_membership(db, normalized_item_id, event_id)
    if membership is not None:
        event = db.get(Event, event_id)
        if event is None:
            raise EventNotFoundError(f"event {event_id} not found")
        return event, False

    item = _get_normalized_item(db, normalized_item_id)
    replaced_memberships = _superseded_memberships(db, item, event_id)
    event = db.scalar(
        select(Event).where(Event.id == event_id).with_for_update()
    )
    if event is None:
        raise EventNotFoundError(f"event {event_id} not found")
    # The optimistic lookup above may race. Recheck after serializing updates
    # on the event row so a waiter observes the membership committed by the
    # lock holder and returns an idempotent result without another revision.
    locked_membership = _existing_membership(db, normalized_item_id, event_id)
    if locked_membership is not None:
        return event, False
    lifecycle_regression = (
        event.event_type == "match"
        and event.lifecycle_status in _MATCH_LIFECYCLE_RANK
        and lifecycle_status in _MATCH_LIFECYCLE_RANK
        and _MATCH_LIFECYCLE_RANK[lifecycle_status]
        < _MATCH_LIFECYCLE_RANK[event.lifecycle_status]
    )
    if lifecycle_regression:
        is_significant_update = False
        lifecycle_status = None
        title = None
        summary = None

    try:
        replaced_normalized_item_ids = [
            predecessor.normalized_item_id
            for predecessor in replaced_memberships
        ]
        for predecessor in replaced_memberships:
            unlink_item_claims_from_event(
                db,
                normalized_item_id=predecessor.normalized_item_id,
                event_id=predecessor.event_id,
            )
            db.delete(predecessor)
        if replaced_memberships:
            db.flush()
        official_confirmation = (
            item.credibility == "official"
            if is_official_confirmation is None
            else is_official_confirmation
        )
        historical_membership = db.scalar(
            select(EventMessage).where(
                EventMessage.event_id == event.id,
                EventMessage.normalized_item_id == item.id,
            )
        )
        membership_values = {
            "relation_type": "primary",
            "membership_role": membership_role,
            "evidence_stance": evidence_stance,
            "independence_key": independence_key or _default_independence_key(item),
            "is_official_confirmation": official_confirmation,
            "is_significant_update": is_significant_update,
            "source_published_at": item.raw_item.published_at,
            "membership_status": "active",
            "withdrawn_at": None,
            "withdrawal_reason": None,
            "source_correction_id": None,
        }
        if historical_membership is None:
            db.add(EventMessage(
                event_id=event.id,
                normalized_item_id=item.id,
                **membership_values,
            ))
        else:
            for key, value in membership_values.items():
                setattr(historical_membership, key, value)
        event.status = "active"
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
        link_item_claims_to_event(
            db,
            normalized_item_id=item.id,
            event_id=event.id,
            relation=evidence_stance,
        )
        _refresh_publish_range(db, event)
        _refresh_editorial_metrics(db, event)
        _refresh_event_importance(db, event)
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
        membership = _existing_membership(db, normalized_item_id, event_id)
        if membership is not None:
            existing_event = db.get(Event, event_id)
            if existing_event is not None:
                return existing_event, False
        raise EventMembershipConflictError(
            "event membership or revision conflicted with another update"
        ) from exc
    if commit:
        db.refresh(event)
    return event, True
