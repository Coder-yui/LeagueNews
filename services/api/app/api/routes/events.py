from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.models.event import Event, EventMessage
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.schemas.event import EventDetailRead, EventMessageRead, EventSummaryRead

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
        "relation_type": message.relation_type,
        "evidence_stance": message.evidence_stance,
        "is_official_confirmation": message.is_official_confirmation,
        "is_significant_update": message.is_significant_update,
        "source_published_at": message.source_published_at,
        "added_at": message.added_at,
        "title": item.translated_title or item.normalized_title,
        "summary": item.summary,
        "source_name": raw_item.source.name,
        "source_url": raw_item.canonical_url,
    }


def _summary_payload(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_key": event.event_key,
        "title": event.title,
        "summary": event.summary,
        "category": event.category,
        "status": event.status,
        "event_type": event.event_type,
        "lifecycle_status": event.lifecycle_status,
        "credibility_status": event.credibility_status,
        "credibility_score": event.credibility_score,
        "importance_score": event.importance_score,
        "importance_evidence": event.importance_evidence,
        "latest_development": event.latest_development,
        "independent_source_count": event.independent_source_count,
        "official_source_count": event.official_source_count,
        "first_published_at": event.first_published_at,
        "last_published_at": event.last_published_at,
        "current_revision": event.current_revision,
        "message_count": len(event.messages),
        "created_at": event.created_at,
        "updated_at": event.updated_at,
    }


def _detail_payload(event: Event) -> dict[str, Any]:
    messages = sorted(
        event.messages,
        key=lambda message: (
            message.source_published_at is not None,
            message.source_published_at or message.added_at,
            message.normalized_item_id,
        ),
        reverse=True,
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
    event = db.scalar(_event_statement().where(Event.id == event_id))
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return event


@router.get("", response_model=list[EventSummaryRead])
def list_events(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    statement = _event_statement().order_by(
        func.coalesce(Event.last_published_at, Event.created_at).desc(),
        Event.id.desc(),
    ).limit(100)
    return [_summary_payload(event) for event in db.scalars(statement)]


@router.get("/{event_id}", response_model=EventDetailRead)
def get_event(event_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _detail_payload(_get_event(db, event_id))


@router.get("/{event_id}/messages", response_model=list[EventMessageRead])
def list_event_messages(
    event_id: int,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return _detail_payload(_get_event(db, event_id))["messages"]
