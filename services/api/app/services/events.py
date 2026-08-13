from datetime import UTC, datetime
from contextlib import nullcontext
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.event_types import (
    AGGREGATION_POLICY_VERSION,
    EVENT_FAMILIES,
    EVENT_LIFECYCLES,
    EVENT_MATERIALITIES,
    EVENT_RELATIONS,
    EVENT_SOURCE_ROLES,
)
from app.models.event import Event, EventMention, EventRevision
from app.models.normalized_item import NormalizedItem
from app.repositories.events import (
    count_active_messages,
    find_mention,
    get_event_for_update,
    get_normalized_item,
)


class EventNotFoundError(ValueError):
    pass


class EventInputError(ValueError):
    pass


class EventMentionConflictError(ValueError):
    pass


def _message_time(item: NormalizedItem) -> datetime:
    value = item.raw_item.published_at or item.raw_item.ingested_at or datetime.now(UTC)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _later(left: datetime | None, right: datetime) -> datetime:
    if left is None:
        return right
    normalized = left.replace(tzinfo=UTC) if left.tzinfo is None else left.astimezone(UTC)
    return max(normalized, right)


def _earlier(left: datetime | None, right: datetime) -> datetime:
    if left is None:
        return right
    normalized = left.replace(tzinfo=UTC) if left.tzinfo is None else left.astimezone(UTC)
    return min(normalized, right)


def _validate_mention_values(
    *,
    mention_index: int,
    relation: str,
    source_role: str,
    materiality: str,
) -> None:
    if mention_index < 0:
        raise EventInputError("mention_index must be non-negative")
    if relation not in EVENT_RELATIONS:
        raise EventInputError(f"unsupported event relation: {relation}")
    if source_role not in EVENT_SOURCE_ROLES:
        raise EventInputError(f"unsupported event source role: {source_role}")
    if materiality not in EVENT_MATERIALITIES:
        raise EventInputError(f"unsupported event materiality: {materiality}")


def _get_published_item(db: Session, normalized_item_id: int) -> NormalizedItem:
    item = get_normalized_item(db, normalized_item_id)
    if item is None:
        raise EventInputError(f"normalized item {normalized_item_id} not found")
    if item.publication_status != "published":
        raise EventInputError("events can only consume published normalized items")
    return item


def _new_mention(
    *,
    event_id: int,
    item: NormalizedItem,
    mention_index: int,
    relation: str,
    source_role: str,
    materiality: str,
    independence_group: str | None,
    evidence_excerpt: str,
    structured_fact_changes: dict[str, Any] | None,
    content_fingerprint: str | None,
    aggregation_policy_version: str,
) -> EventMention:
    return EventMention(
        event_id=event_id,
        normalized_item_id=item.id,
        normalized_item_revision=item.current_revision,
        mention_index=mention_index,
        aggregation_policy_version=aggregation_policy_version,
        relation=relation,
        source_role=source_role,
        independence_group=independence_group,
        materiality=materiality,
        evidence_excerpt=evidence_excerpt,
        structured_fact_changes=structured_fact_changes or {},
        content_fingerprint=content_fingerprint,
        source_reliability_snapshot=item.raw_item.source.reliability_score,
        source_published_at=item.raw_item.published_at,
    )


def create_event(
    db: Session,
    *,
    normalized_item_id: int,
    mention_index: int,
    event_family: str,
    products: list[str],
    canonical_anchors: dict[str, Any],
    title: str,
    current_summary: str,
    relation: str = "reports",
    source_role: str = "unknown",
    materiality: str = "material_update",
    independence_group: str | None = None,
    evidence_excerpt: str = "",
    structured_fact_changes: dict[str, Any] | None = None,
    content_fingerprint: str | None = None,
    lifecycle_status: str = "developing",
    latest_development: str = "",
    key_facts: list[dict[str, Any]] | None = None,
    aggregation_policy_version: str = AGGREGATION_POLICY_VERSION,
    commit: bool = True,
    use_savepoint: bool = True,
) -> tuple[Event, bool]:
    _validate_mention_values(
        mention_index=mention_index,
        relation=relation,
        source_role=source_role,
        materiality=materiality,
    )
    if event_family not in EVENT_FAMILIES:
        raise EventInputError(f"unsupported event family: {event_family}")
    if lifecycle_status not in EVENT_LIFECYCLES:
        raise EventInputError(f"unsupported event lifecycle: {lifecycle_status}")
    item = _get_published_item(db, normalized_item_id)
    existing = find_mention(
        db,
        normalized_item_id=normalized_item_id,
        normalized_item_revision=item.current_revision,
        mention_index=mention_index,
        aggregation_policy_version=aggregation_policy_version,
    )
    if existing is not None:
        event = db.get(Event, existing.event_id)
        if event is None:
            raise EventMentionConflictError("idempotent mention points to a missing event")
        return event, False

    if materiality != "material_update":
        raise EventInputError("a new event requires a material update")
    if source_role == "responsible_official" and (
        not item.raw_item.source.is_official or item.content_form == "repost"
    ):
        raise EventInputError(
            "responsible_official requires a direct message from an official source"
        )
    observed_at = _message_time(item)
    event = Event(
        title=title,
        current_summary=current_summary,
        event_family=event_family,
        products=list(products),
        canonical_anchors=dict(canonical_anchors),
        lifecycle_status=lifecycle_status,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        last_material_update_at=(observed_at if materiality == "material_update" else None),
        latest_development=latest_development,
        key_facts=list(key_facts or []),
        origin_message_id=item.id,
        latest_update_message_id=(item.id if materiality == "material_update" else None),
        aggregation_policy_version=aggregation_policy_version,
        message_count_total=1,
        importance_score=0.0,
        importance_breakdown={},
    )
    try:
        with db.begin_nested() if use_savepoint else nullcontext():
            db.add(event)
            db.flush()
            mention = _new_mention(
                event_id=event.id,
                item=item,
                mention_index=mention_index,
                relation=relation,
                source_role=source_role,
                materiality=materiality,
                independence_group=independence_group,
                evidence_excerpt=evidence_excerpt,
                structured_fact_changes=structured_fact_changes,
                content_fingerprint=content_fingerprint,
                aggregation_policy_version=aggregation_policy_version,
            )
            db.add(mention)
            db.flush()
            db.add(
                EventRevision(
                    event_id=event.id,
                    revision=1,
                    title=event.title,
                    summary=event.current_summary,
                    change_note="创建事件",
                    evidence_snapshot={
                        "normalized_item_id": item.id,
                        "mention_index": mention_index,
                        "relation": relation,
                        "materiality": materiality,
                    },
                )
            )
        if commit:
            db.commit()
            db.refresh(event)
        return event, True
    except IntegrityError as exc:
        if not use_savepoint:
            raise EventMentionConflictError("event identity or mention conflicted") from exc
        if commit:
            db.rollback()
        existing = find_mention(
            db,
            normalized_item_id=normalized_item_id,
            normalized_item_revision=item.current_revision,
            mention_index=mention_index,
            aggregation_policy_version=aggregation_policy_version,
        )
        if existing is not None:
            existing_event = db.get(Event, existing.event_id)
            if existing_event is not None:
                return existing_event, False
        raise EventMentionConflictError("event identity or mention conflicted") from exc


def add_event_mention(
    db: Session,
    *,
    event_id: int,
    normalized_item_id: int,
    mention_index: int,
    relation: str,
    source_role: str,
    materiality: str,
    independence_group: str | None = None,
    evidence_excerpt: str = "",
    structured_fact_changes: dict[str, Any] | None = None,
    content_fingerprint: str | None = None,
    title: str | None = None,
    current_summary: str | None = None,
    latest_development: str | None = None,
    lifecycle_status: str | None = None,
    canonical_anchors: dict[str, Any] | None = None,
    key_facts: list[dict[str, Any]] | None = None,
    aggregation_policy_version: str = AGGREGATION_POLICY_VERSION,
    commit: bool = True,
    use_savepoint: bool = True,
) -> tuple[Event, bool]:
    _validate_mention_values(
        mention_index=mention_index,
        relation=relation,
        source_role=source_role,
        materiality=materiality,
    )
    if lifecycle_status is not None and lifecycle_status not in EVENT_LIFECYCLES:
        raise EventInputError(f"unsupported event lifecycle: {lifecycle_status}")
    item = _get_published_item(db, normalized_item_id)
    existing = find_mention(
        db,
        normalized_item_id=normalized_item_id,
        normalized_item_revision=item.current_revision,
        mention_index=mention_index,
        aggregation_policy_version=aggregation_policy_version,
    )
    if existing is not None:
        if existing.event_id != event_id:
            raise EventMentionConflictError(
                "mention idempotency key is already attached to another event"
            )
        event = db.get(Event, event_id)
        if event is None:
            raise EventNotFoundError(f"event {event_id} not found")
        return event, False

    if source_role == "responsible_official" and (
        not item.raw_item.source.is_official or item.content_form == "repost"
    ):
        raise EventInputError(
            "responsible_official requires a direct message from an official source"
        )
    event = get_event_for_update(db, event_id)
    if event is None:
        raise EventNotFoundError(f"event {event_id} not found")
    observed_at = _message_time(item)

    try:
        mention = _new_mention(
            event_id=event.id,
            item=item,
            mention_index=mention_index,
            relation=relation,
            source_role=source_role,
            materiality=materiality,
            independence_group=independence_group,
            evidence_excerpt=evidence_excerpt,
            structured_fact_changes=structured_fact_changes,
            content_fingerprint=content_fingerprint,
            aggregation_policy_version=aggregation_policy_version,
        )
        with db.begin_nested() if use_savepoint else nullcontext():
            db.add(mention)
            db.flush()
        event.first_seen_at = _earlier(event.first_seen_at, observed_at)
        event.last_seen_at = _later(event.last_seen_at, observed_at)
        event.message_count_total = count_active_messages(db, event.id)
        mentions = list(event.mentions)
        if all(existing.id != mention.id for existing in mentions):
            mentions.append(mention)
        if materiality == "material_update":
            event.last_material_update_at = _later(event.last_material_update_at, observed_at)
            event.latest_update_message_id = item.id
            event.current_revision += 1
            if title is not None:
                event.title = title
            if current_summary is not None:
                event.current_summary = current_summary
            if latest_development is not None:
                event.latest_development = latest_development
            if lifecycle_status is not None:
                event.lifecycle_status = lifecycle_status
            if canonical_anchors is not None:
                event.canonical_anchors = dict(canonical_anchors)
            if key_facts is not None:
                event.key_facts = list(key_facts)
            db.add(
                EventRevision(
                    event_id=event.id,
                    revision=event.current_revision,
                    title=event.title,
                    summary=event.current_summary,
                    change_note=latest_development or "事件实质更新",
                    evidence_snapshot={
                        "normalized_item_id": item.id,
                        "mention_index": mention_index,
                        "relation": relation,
                        "materiality": materiality,
                    },
                )
            )
        if commit:
            db.commit()
            db.refresh(event)
        return event, True
    except IntegrityError as exc:
        if not use_savepoint:
            raise EventMentionConflictError(
                "event mention conflicted with another update"
            ) from exc
        if commit:
            db.rollback()
        existing = find_mention(
            db,
            normalized_item_id=normalized_item_id,
            normalized_item_revision=item.current_revision,
            mention_index=mention_index,
            aggregation_policy_version=aggregation_policy_version,
        )
        if existing is not None and existing.event_id == event_id:
            existing_event = db.get(Event, event_id)
            if existing_event is not None:
                return existing_event, False
        raise EventMentionConflictError("event mention conflicted with another update") from exc
