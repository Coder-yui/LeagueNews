from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CandidateRejection(BaseModel):
    event_id: int = Field(ge=1)
    reason: str = Field(min_length=1)


class EventDecisionDraft(BaseModel):
    decision: Literal["not_event", "create", "update"]
    reason: str = Field(min_length=1)
    candidate_event_id: int | None = None
    candidate_rejections: list[CandidateRejection] = Field(
        default_factory=list,
        max_length=5,
    )
    event_key: str | None = Field(default=None, max_length=160)
    event_type: Literal[
        "patch",
        "major_gameplay_change",
        "match",
        "transfer",
        "roster",
        "release",
        "activity",
        "incident",
        "tournament",
        "other",
    ] = "other"
    lifecycle_status: Literal[
        "scheduled",
        "live",
        "developing",
        "unconfirmed",
        "confirmed",
        "completed",
        "resolved",
        "disputed",
        "expired_unconfirmed",
        "officially_refuted",
    ] | None = None
    evidence_stance: Literal["supports", "contradicts", "context"] = "supports"
    update_kind: Literal[
        "new_fact",
        "confirmation",
        "refutation",
        "correction",
        "context",
        "duplicate_evidence",
    ] = "new_fact"
    official_confirmation: bool = False
    title: str | None = Field(default=None, max_length=500)
    summary: str | None = None
    category: str | None = Field(default=None, max_length=60)
    change_note: str | None = None
    new_facts: list[str] = Field(default_factory=list)
    latest_development: str | None = None
    importance_score: float | None = Field(default=None, ge=0, le=1)
    importance_evidence: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_decision_fields(self) -> "EventDecisionDraft":
        if self.decision == "not_event":
            if self.candidate_event_id is not None:
                raise ValueError("not_event cannot reference a candidate event")
        elif self.decision == "create":
            if self.candidate_event_id is not None:
                raise ValueError("create cannot reference a candidate event")
            if not self.title or not self.summary or not self.category:
                raise ValueError("create requires title, summary and category")
        elif self.candidate_event_id is None:
            raise ValueError("update requires candidate_event_id")
        return self


class EventAggregationRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    normalized_item_id: int
    supersedes_run_id: int | None
    status: str
    outcome: str | None
    current_stage: str
    execution_mode: str
    correction_id: int | None
    restart_from_stage: str | None
    candidate_snapshot: list[dict[str, Any]]
    decision_draft: dict[str, Any]
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class EventReviewTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_aggregation_run_id: int
    status: str
    proposal: dict[str, Any]
    feedback: dict[str, Any]
    decision_source: str
    policy_version: str | None
    created_at: datetime
    resolved_at: datetime | None


class EventReviewApproval(BaseModel):
    note: str | None = None


class EventReviewRejection(BaseModel):
    reason: str = Field(min_length=1)
    knowledge_rule: str | None = None
    knowledge_scope: str = Field(default="global", min_length=1, max_length=160)
