from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class EventSummaryRead(BaseModel):
    id: int
    aggregation_key: str | None
    title: str
    summary: str
    status: str
    event_kind: str
    aggregation_strategy: str
    product_scope: str
    lifecycle_status: str
    credibility_status: str
    credibility_score: float
    importance_score: float
    importance_evidence: list[str]
    importance_dimensions: dict[str, Any]
    importance_policy_version: str
    latest_development: str
    independent_source_count: int
    supporting_source_count: int
    contradicting_source_count: int
    official_source_count: int
    first_published_at: datetime | None
    last_published_at: datetime | None
    current_revision: int
    message_count: int
    created_at: datetime
    updated_at: datetime


class EventMessageRead(BaseModel):
    normalized_item_id: int
    membership_role: str
    evidence_stance: str
    is_official_evidence: bool
    source_reliability_snapshot: float
    timeline_note: str
    update_kind: str
    is_significant_update: bool
    importance_contribution: float
    importance_contribution_evidence: list[str]
    source_published_at: datetime | None
    added_at: datetime
    title: str
    summary: str
    source_name: str
    source_url: str | None


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


class EventPageRead(BaseModel):
    items: list[EventSummaryRead]
    total: int


class EventAdminUpdate(BaseModel):
    lifecycle_status: str | None = Field(default=None, min_length=1, max_length=40)
    summary: str | None = Field(default=None, min_length=1)
    latest_development: str | None = None
    change_note: str = Field(default="管理台手动更新", min_length=1)


class EventMembershipAdminCreate(BaseModel):
    membership_role: str = Field(default="primary", pattern="^(primary|component|cross_ref)$")
    evidence_stance: str = Field(default="supports", pattern="^(supports|contradicts|context)$")
    timeline_note: str = Field(default="管理台手动归属", min_length=1)
    update_kind: Literal[
        "new_fact",
        "confirmation",
        "refutation",
        "correction",
        "context",
        "duplicate_evidence",
    ] = "new_fact"
