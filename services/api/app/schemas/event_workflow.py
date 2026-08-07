from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CandidateRejection(BaseModel):
    event_id: int = Field(ge=1)
    reason: str = Field(min_length=1)


class EventMembershipDraft(BaseModel):
    target: str = Field(min_length=1, max_length=80)
    event_kind: Literal[
        "gameplay_update",
        "gameplay_release",
        "cosmetic_release",
        "roster_change",
        "esports_match",
        "esports_schedule",
        "qualification_change",
        "commercial_offer",
        "player_activity",
        "service_incident",
        "disciplinary_action",
        "security_notice",
        "media_release",
        "corporate_announcement",
        "community_activity",
        "other",
    ] = "other"
    aggregation_strategy: Literal[
        "timeline",
        "patch_cycle",
        "calendar_day",
        "recurring_window",
        "release",
        "singleton",
    ] = "singleton"
    product_scope: Literal[
        "lol_pc",
        "lol_esports",
        "tft",
        "lol_universe",
        "riot_corporate",
        "lol_merch_music",
        "uncertain",
    ] = "uncertain"
    aggregation_key: str = Field(min_length=1, max_length=255)
    identity_resolution: Literal[
        "exact_key", "semantic_candidate", "new_event"
    ] = "new_event"
    identity_rationale: str | None = Field(default=None, max_length=500)
    membership_role: Literal["primary", "component", "cross_ref"] = "primary"
    evidence_stance: Literal["supports", "contradicts", "context"] = "supports"
    update_kind: Literal[
        "new_fact",
        "confirmation",
        "refutation",
        "correction",
        "context",
        "duplicate_evidence",
    ] = "new_fact"
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
    timeline_note: str = Field(min_length=1)

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        if value == "new":
            return value
        if not value.startswith("existing:"):
            raise ValueError("target must be new or existing:{event_id}")
        try:
            event_id = int(value.split(":", 1)[1])
        except ValueError as exc:
            raise ValueError("existing target requires an integer event_id") from exc
        if event_id < 1:
            raise ValueError("existing target requires a positive event_id")
        return value

    @property
    def existing_event_id(self) -> int | None:
        return (
            int(self.target.split(":", 1)[1])
            if self.target.startswith("existing:")
            else None
        )


class EventDecisionDraft(BaseModel):
    memberships: list[EventMembershipDraft] = Field(
        default_factory=list,
        max_length=12,
    )
    candidate_rejections: list[CandidateRejection] = Field(
        default_factory=list,
        max_length=8,
    )

    @model_validator(mode="after")
    def validate_memberships(self) -> "EventDecisionDraft":
        identities = [
            (membership.target, membership.aggregation_key)
            for membership in self.memberships
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("memberships cannot contain duplicate targets and keys")
        existing_targets = [
            membership.target
            for membership in self.memberships
            if membership.existing_event_id is not None
        ]
        if len(existing_targets) != len(set(existing_targets)):
            raise ValueError("memberships cannot repeat an existing event target")
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


class EventReviewCorrectionApproval(BaseModel):
    decision_draft: dict[str, Any]
    note: str | None = None


class EventReviewRejection(BaseModel):
    reason: str = Field(min_length=1)
    knowledge_rule: str | None = None
    knowledge_scope: str = Field(default="global", min_length=1, max_length=160)
