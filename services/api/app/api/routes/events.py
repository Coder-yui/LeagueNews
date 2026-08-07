from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.models.event import Event, EventMessage, EventRevision
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.schemas.event import (
    EventAdminUpdate,
    EventDetailRead,
    EventMembershipAdminCreate,
    EventMessageRead,
    EventPageRead,
    EventSummaryRead,
)
from app.schemas.event_workflow import EventAggregationRunRead
from app.services.event_aggregation import add_message_to_event, refresh_event_metrics
from app.services.llm import LLMAnalysisError, LLMConfigurationError
from app.workflows.event_aggregation import start_event_aggregation

router = APIRouter()


def _event_statement():
    return select(Event).options(
        selectinload(Event.messages)
        .selectinload(EventMessage.normalized_item)
        .selectinload(NormalizedItem.raw_item)
        .selectinload(RawItem.source),
        selectinload(Event.revisions),
    )


def _message_payload(message: EventMessage) -> dict[str, Any]:
    item = message.normalized_item
    raw_item = item.raw_item
    return {
        "normalized_item_id": item.id,
        "membership_role": message.membership_role,
        "evidence_stance": message.evidence_stance,
        "is_official_evidence": message.is_official_evidence,
        "source_reliability_snapshot": message.source_reliability_snapshot,
        "timeline_note": message.timeline_note,
        "update_kind": message.update_kind,
        "is_significant_update": message.is_significant_update,
        "importance_contribution": message.importance_contribution,
        "importance_contribution_evidence": (
            message.importance_contribution_evidence
        ),
        "source_published_at": message.source_published_at,
        "added_at": message.added_at,
        "title": item.translated_title or item.normalized_title,
        "summary": item.summary,
        "source_name": raw_item.source.name,
        "source_url": raw_item.canonical_url,
    }


def _summary_payload(event: Event) -> dict[str, Any]:
    active_messages = [
        message
        for message in event.messages
        if message.membership_status == "active"
        and message.normalized_item.publication_status == "published"
    ]
    return {
        "id": event.id,
        "aggregation_key": event.aggregation_key,
        "title": event.title,
        "summary": event.summary,
        "status": event.status,
        "event_kind": event.event_kind,
        "aggregation_strategy": event.aggregation_strategy,
        "product_scope": event.product_scope,
        "lifecycle_status": event.lifecycle_status,
        "credibility_status": event.credibility_status,
        "credibility_score": event.credibility_score,
        "importance_score": event.importance_score,
        "importance_evidence": event.importance_evidence,
        "importance_dimensions": event.importance_dimensions,
        "importance_policy_version": event.importance_policy_version,
        "latest_development": event.latest_development,
        "independent_source_count": event.independent_source_count,
        "supporting_source_count": event.supporting_source_count,
        "contradicting_source_count": event.contradicting_source_count,
        "official_source_count": event.official_source_count,
        "first_published_at": event.first_published_at,
        "last_published_at": event.last_published_at,
        "current_revision": event.current_revision,
        "message_count": len(active_messages),
        "created_at": event.created_at,
        "updated_at": event.updated_at,
    }


def _detail_payload(event: Event) -> dict[str, Any]:
    messages = sorted(
        (
            message
            for message in event.messages
            if message.membership_status == "active"
            and message.normalized_item.publication_status == "published"
        ),
        key=lambda message: (
            message.source_published_at is None,
            message.source_published_at or message.added_at,
            message.normalized_item_id,
        ),
        reverse=False,
    )
    return {
        **_summary_payload(event),
        "messages": [_message_payload(message) for message in messages],
        "revisions": [
            {
                "id": revision.id,
                "revision": revision.revision,
                "title": revision.title,
                "summary": revision.summary,
                "change_note": revision.change_note,
                "evidence_snapshot": revision.evidence_snapshot,
                "created_at": revision.created_at,
            }
            for revision in event.revisions
        ],
    }


def _get_event(db: Session, event_id: int) -> Event:
    event = db.scalar(
        _event_statement().where(
            Event.id == event_id,
            Event.status == "active",
        )
    )
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return event


@router.get("", response_model=list[EventSummaryRead])
def list_events(
    event_kind: str | None = None,
    lifecycle_status: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    statement = _event_statement().where(Event.status == "active")
    if event_kind:
        statement = statement.where(Event.event_kind == event_kind)
    if lifecycle_status:
        statement = statement.where(Event.lifecycle_status == lifecycle_status)
    statement = statement.order_by(
        Event.importance_score.desc(),
        func.coalesce(Event.last_published_at, Event.created_at).desc(),
        Event.id.desc(),
    ).offset(offset).limit(limit)
    return [_summary_payload(event) for event in db.scalars(statement)]


@router.get("/page", response_model=EventPageRead)
def list_events_page(
    event_kind: str | None = None,
    lifecycle_status: str | None = None,
    search: str | None = None,
    sort: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conditions = [Event.status == "active"]
    if event_kind:
        conditions.append(Event.event_kind == event_kind)
    if lifecycle_status:
        conditions.append(Event.lifecycle_status == lifecycle_status)
    if search:
        search_value = search.strip()
        if search_value.isdigit():
            conditions.append(Event.id == int(search_value))
        else:
            conditions.append(
                or_(
                    Event.title.ilike(f"%{search_value}%"),
                    Event.summary.ilike(f"%{search_value}%"),
                )
            )
    total = db.scalar(select(func.count(Event.id)).where(*conditions)) or 0
    time_column = func.coalesce(Event.last_published_at, Event.created_at)
    ordering = ((Event.importance_score.asc(), time_column.asc(), Event.id.asc()) if sort == "asc" else (Event.importance_score.desc(), time_column.desc(), Event.id.desc()))
    statement = (
        _event_statement()
        .where(*conditions)
        .order_by(*ordering)
        .offset(offset)
        .limit(limit)
    )
    return {
        "items": [_summary_payload(event) for event in db.scalars(statement)],
        "total": total,
    }


@router.get("/{event_id}", response_model=EventDetailRead)
def get_event(event_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _detail_payload(_get_event(db, event_id))


@router.get("/{event_id}/messages", response_model=list[EventMessageRead])
def list_event_messages(
    event_id: int,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return _detail_payload(_get_event(db, event_id))["messages"]


@router.patch("/{event_id}", response_model=EventDetailRead)
def update_event(
    event_id: int,
    payload: EventAdminUpdate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    event = _get_event(db, event_id)
    updates = payload.model_dump(exclude_unset=True, exclude={"change_note"})
    if not updates:
        raise HTTPException(status_code=409, detail="no event fields supplied")
    for field, value in updates.items():
        setattr(event, field, value)
    event.current_revision += 1
    event.revisions.append(
        EventRevision(
            revision=event.current_revision,
            title=event.title,
            summary=event.summary,
            change_note=payload.change_note,
            evidence_snapshot={
                "source": "admin",
                "updated_fields": sorted(updates),
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
    )
    db.commit()
    return _detail_payload(_get_event(db, event_id))


@router.post("/{event_id}/messages/{item_id}", response_model=EventDetailRead)
def add_event_message(
    event_id: int,
    item_id: int,
    payload: EventMembershipAdminCreate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _get_event(db, event_id)
    try:
        add_message_to_event(
            db,
            event_id=event_id,
            normalized_item_id=item_id,
            membership_role=payload.membership_role,
            evidence_stance=payload.evidence_stance,
            timeline_note=payload.timeline_note,
            update_kind=payload.update_kind,
            latest_development=payload.timeline_note,
            change_note=payload.timeline_note,
            evidence={"source": "admin"},
        )
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _detail_payload(_get_event(db, event_id))


@router.post("/{event_id}/reaggregate", response_model=EventAggregationRunRead)
async def reaggregate_event(
    event_id: int,
    db: Session = Depends(get_db),
) -> object:
    event = _get_event(db, event_id)
    active_memberships = [
        membership
        for membership in event.messages
        if membership.membership_status == "active"
    ]
    if not active_memberships:
        raise HTTPException(status_code=409, detail="event has no active messages")
    latest = max(
        active_memberships,
        key=lambda membership: (
            membership.source_published_at or membership.added_at,
            membership.normalized_item_id,
        ),
    )
    try:
        return await start_event_aggregation(db, latest.normalized_item)
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMAnalysisError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{event_id}/messages/{item_id}", response_model=EventDetailRead)
def withdraw_event_message(
    event_id: int,
    item_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    event = _get_event(db, event_id)
    membership = db.get(EventMessage, (event_id, item_id))
    if membership is None or membership.membership_status != "active":
        raise HTTPException(status_code=404, detail="active event membership not found")
    membership.membership_status = "withdrawn"
    membership.withdrawn_at = datetime.now(UTC)
    membership.withdrawal_reason = "admin manual unlink"
    refresh_event_metrics(db, event)
    event.current_revision += 1
    event.revisions.append(
        EventRevision(
            revision=event.current_revision,
            title=event.title,
            summary=event.summary,
            change_note=f"管理台解除消息 {item_id} 关联",
            evidence_snapshot={"source": "admin", "withdrawn_normalized_item_id": item_id},
        )
    )
    db.commit()
    return _detail_payload(_get_event(db, event_id))
