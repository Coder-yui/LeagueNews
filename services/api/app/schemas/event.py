from datetime import datetime
from typing import Any

from pydantic import BaseModel


class EventSourceRead(BaseModel):
    message_id: int
    source_id: int
    source_name: str
    source_url: str | None
    published_at: datetime | None


class EventCardRead(BaseModel):
    id: int
    title: str
    current_summary: str
    products: list[str]
    event_family: str
    category: str
    lifecycle_status: str
    importance_score: float
    importance_level: str
    credibility_score: float
    credibility_level: str
    heat_score: float
    heat_level: str
    message_count: int
    source_count: int
    message_count_total: int
    message_count_24h: int
    unique_sources_24h: int
    last_material_update_at: datetime | None
    primary_source: EventSourceRead | None
    best_media_url: str | None


class EventTimelineNodeRead(BaseModel):
    mention_id: int
    message_id: int
    message_revision: int
    occurred_at: datetime
    relation: str
    title: str
    note: str
    structured_fact_changes: dict[str, Any]
    source_id: int
    source_name: str


class EventEvidenceRead(BaseModel):
    mention_id: int
    message_id: int
    message_revision: int
    relation: str
    source_role: str
    materiality: str
    independence_group: str | None
    evidence_excerpt: str
    source_id: int
    source_name: str
    source_url: str | None
    published_at: datetime | None
    content_form: str


class EventRelatedMessageRead(BaseModel):
    message_id: int
    title: str
    summary: str
    source_id: int
    source_name: str
    source_url: str | None
    published_at: datetime | None
    content_form: str


class EventDetailRead(EventCardRead):
    latest_development: str
    key_facts: list[dict[str, Any]]
    canonical_anchors: dict[str, Any]
    importance_breakdown: dict[str, Any]
    credibility_breakdown: dict[str, Any]
    heat_breakdown: dict[str, Any]
    timeline: list[EventTimelineNodeRead]
    evidence: list[EventEvidenceRead]
    related_messages: list[EventRelatedMessageRead]
    references: dict[str, int | None]


class EventPageRead(BaseModel):
    items: list[EventCardRead]
    total: int
    product_options: list[str]
    event_family_options: list[str]
    lifecycle_options: list[str]
    credibility_options: list[str]
    category_options: list[str]
