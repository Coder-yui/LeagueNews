from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.event_types import EventFamily, EventMateriality, EventRelation, EventSourceRole
from app.domain.esports_match_identity import (
    esports_match_attach_subject,
    normalized_match_participants,
    placeholder_match_participants,
)
from app.domain.message_taxonomy import Product


class NewEventSeed(BaseModel):
    """The minimum description needed to create an Event."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1)
    canonical_anchors: dict[str, Any] = Field(default_factory=dict)
    latest_development: str = ""
    key_facts: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class EventProjectionProposal(BaseModel):
    """Optional presentation changes, evaluated only after membership is chosen."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    summary: str | None = Field(default=None, min_length=1)
    latest_development: str | None = None
    key_facts: list[dict[str, Any]] | None = Field(default=None, max_length=20)


class EsportsMatchIdentity(BaseModel):
    """Explicit occurrence facts used to validate esports_match membership."""

    model_config = ConfigDict(extra="forbid")

    participants: list[str] = Field(default_factory=list, max_length=16)
    competition: str | None = Field(default=None, max_length=300)
    stage: str | None = Field(default=None, max_length=200)
    round: str | None = Field(default=None, max_length=200)
    match_date: date | None = None
    scheduled_at: datetime | None = None
    series_format: str | None = Field(default=None, max_length=40)
    external_match_id: str | None = Field(default=None, max_length=300)

    @field_validator(
        "competition", "stage", "round", "series_format", "external_match_id"
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("participants")
    @classmethod
    def normalize_participants(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]


class EventMentionDecision(BaseModel):
    """One semantic membership decision made from the message and recalled candidates."""

    model_config = ConfigDict(extra="forbid")

    mention_index: int = Field(ge=0)
    action: Literal["attach", "create", "ignore"]
    event_id: int | None = Field(default=None, ge=1)
    # A message may cover multiple products, but each event mention is routed
    # to one product domain. The workflow can infer this only for single-product
    # messages; cross-product messages must carry it explicitly.
    product: Product | None = None
    event_family: EventFamily | None = None
    relation: EventRelation = "reports"
    source_role: EventSourceRole = "unknown"
    materiality: EventMateriality = "material_update"
    evidence_excerpt: str = Field(default="", max_length=2000)
    match_identity: EsportsMatchIdentity | None = None
    new_event: NewEventSeed | None = None
    projection: EventProjectionProposal | None = None

    @model_validator(mode="after")
    def validate_action_contract(self) -> "EventMentionDecision":
        if self.action != "ignore" and self.event_family is None:
            raise ValueError("create/attach requires event_family")
        if self.event_family != "esports_match" and self.match_identity is not None:
            raise ValueError("match identity metadata is only valid for esports_match")
        if (
            self.action != "ignore"
            and self.event_family == "esports_match"
            and self.match_identity is None
        ):
            raise ValueError("esports_match create/attach requires match_identity")
        if self.action != "ignore" and self.event_family == "esports_match":
            identity = self.match_identity.model_dump(mode="json", exclude_none=True)
            placeholders = placeholder_match_participants(identity)
            if placeholders:
                # A participant subject must name real teams; "未知对手"/"TBD" style
                # placeholders explicitly state the side is unknown and can never
                # satisfy the subject contract for either action.
                raise ValueError(
                    "esports_match match_identity participants must name real teams; "
                    f"placeholder values are not participants: {placeholders}. "
                    "对手未知时不能 create（应 attach 已有候选或 ignore）"
                )
            if self.action == "create":
                # A user-visible concrete match Event names both sides. An
                # external_match_id is additional strong identity evidence but never
                # substitutes for the two participants.
                if len(normalized_match_participants(identity.get("participants"))) != 2:
                    raise ValueError(
                        "esports_match create requires exactly 2 distinct participants "
                        "as the match subject (external_match_id alone is not enough)"
                    )
            elif not esports_match_attach_subject(identity):
                # Follow-up evidence may name only one side, but an identity with no
                # participant at all cannot attach to a concrete match.
                raise ValueError(
                    "esports_match attach requires match_identity with at least "
                    "1 explicit participant"
                )
        if self.action == "create":
            if self.event_id is not None:
                raise ValueError("create cannot reference event_id")
            if self.new_event is None:
                raise ValueError("create requires new_event")
            if self.projection is not None:
                raise ValueError("create uses new_event, not projection")
            if self.materiality != "material_update":
                raise ValueError("create requires material_update")
        elif self.action == "attach":
            if self.event_id is None:
                raise ValueError("attach requires event_id")
            if self.new_event is not None:
                raise ValueError("attach cannot include new_event")
            if self.materiality != "material_update" and self.projection is not None:
                raise ValueError("non-material attach cannot change the event projection")
            if self.materiality == "material_update" and (
                self.projection is None or self.projection.latest_development is None
            ):
                raise ValueError(
                    "material_update attach requires projection with latest_development"
                )
        else:
            if (
                self.event_id is not None
                or self.new_event is not None
                or self.projection is not None
                or self.match_identity is not None
            ):
                raise ValueError("ignore cannot reference or change an event")
        if self.action != "ignore" and not self.evidence_excerpt.strip():
            raise ValueError("create/attach requires evidence_excerpt")
        return self


class EventAggregationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentions: list[EventMentionDecision] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_mention_indexes(self) -> "EventAggregationResult":
        indexes = [mention.mention_index for mention in self.mentions]
        if indexes != list(range(len(indexes))):
            raise ValueError("mention_index must be unique, ordered, and contiguous from zero")
        return self
