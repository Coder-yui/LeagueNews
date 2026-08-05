from datetime import datetime
from typing import Any

from pydantic import BaseModel


class EventSummaryRead(BaseModel):
    id: int
    event_key: str | None
    aggregation_key: str | None
    title: str
    summary: str
    category: str
    status: str
    event_type: str
    lifecycle_status: str
    credibility_status: str
    credibility_score: float
    importance_score: float
    importance_evidence: list[str]
    latest_development: str
    independent_source_count: int
    official_source_count: int
    first_published_at: datetime | None
    last_published_at: datetime | None
    current_revision: int
    message_count: int
    created_at: datetime
    updated_at: datetime


class EventMessageRead(BaseModel):
    normalized_item_id: int
    relation_type: str
    membership_role: str
    evidence_stance: str
    is_official_confirmation: bool
    is_significant_update: bool
    source_published_at: datetime | None
    added_at: datetime
    title: str
    summary: str
    source_name: str
    source_url: str | None
    credibility_score: float
    credibility_components: dict[str, Any]


class EventRevisionRead(BaseModel):
    id: int
    revision: int
    title: str
    summary: str
    change_note: str
    evidence_snapshot: dict[str, Any]
    created_at: datetime


class EventDetailRead(EventSummaryRead):
    messages: list[EventMessageRead]
    revisions: list[EventRevisionRead]
