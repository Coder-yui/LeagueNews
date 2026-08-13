from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.event_types import EventFamily, EventMateriality, EventRelation, EventSourceRole
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
    new_event: NewEventSeed | None = None
    projection: EventProjectionProposal | None = None

    @model_validator(mode="after")
    def validate_action_contract(self) -> "EventMentionDecision":
        if self.action != "ignore" and self.event_family is None:
            raise ValueError("create/attach requires event_family")
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
        else:
            if self.event_id is not None or self.new_event is not None or self.projection is not None:
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
