import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.event_types import EventFamily, EventMateriality, EventRelation, EventSourceRole
from app.domain.importance import (
    CompetitionRegion,
    ImportanceProfile,
    ImportanceScale,
    Prominence,
    SkinTier,
)


class EventImportanceSemantics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: ImportanceProfile
    scale: ImportanceScale = "standard"
    competition_region: CompetitionRegion = "none"
    prominence: Prominence = "normal"
    skin_tier: SkinTier = "none"
    is_bulk_update: bool = False


class KeyFactChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    add: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    replace: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    remove: list[str] = Field(default_factory=list, max_length=12)

    def has_changes(self) -> bool:
        return bool(self.add or self.replace or self.remove)


class CandidateRejection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class EventMentionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mention_index: int = Field(ge=0)
    event_family: EventFamily
    action: Literal["create", "update", "ignore"]
    candidate_event_id: int | None = None
    relation: EventRelation
    source_role: EventSourceRole
    materiality: EventMateriality
    canonical_anchors: dict[str, Any] = Field(default_factory=dict)
    event_title: str | None = Field(default=None, max_length=500)
    proposed_summary: str | None = None
    latest_development: str | None = None
    key_fact_changes: KeyFactChanges = Field(default_factory=KeyFactChanges)
    importance: EventImportanceSemantics | None = None
    evidence_excerpt: str = Field(default="", max_length=2000)
    candidate_rejections: list[CandidateRejection] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_action_contract(self) -> "EventMentionDecision":
        if self.action == "create":
            if self.candidate_event_id is not None:
                raise ValueError("create cannot include candidate_event_id")
            if not self.canonical_anchors:
                raise ValueError("create requires canonical_anchors")
            if not (self.event_title or "").strip() or not (
                self.proposed_summary or ""
            ).strip():
                raise ValueError("create requires event_title and proposed_summary")
            if self.materiality != "material_update":
                raise ValueError("create requires material_update")
        elif self.action == "update":
            if self.candidate_event_id is None:
                raise ValueError("update requires candidate_event_id")
            if not self.evidence_excerpt.strip():
                raise ValueError("update requires evidence_excerpt")

        if (
            self.action in {"create", "update"}
            and self.materiality == "material_update"
            and self.importance is None
        ):
            raise ValueError("material create/update requires importance semantics")

        if self.materiality != "material_update" and (
            self.event_title is not None
            or self.proposed_summary is not None
            or self.latest_development is not None
            or self.key_fact_changes.has_changes()
            or self.importance is not None
        ):
            raise ValueError("non-material mentions cannot change the event projection")
        return self


class EventAggregationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentions: list[EventMentionDecision] = Field(default_factory=list, max_length=12)
    ignored_fragments: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_mention_indexes(self) -> "EventAggregationResult":
        indexes = [mention.mention_index for mention in self.mentions]
        if indexes != list(range(len(indexes))):
            raise ValueError("mention_index must be unique, ordered, and contiguous from zero")
        create_identities: dict[tuple[str, str], int] = {}
        for mention in self.mentions:
            if mention.action != "create":
                continue
            identity = (
                mention.event_family,
                json.dumps(
                    mention.canonical_anchors,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            previous_index = create_identities.get(identity)
            if previous_index is not None:
                raise ValueError(
                    "create mentions with the same family and anchors must be merged "
                    f"into one event: indexes {previous_index} and {mention.mention_index}"
                )
            create_identities[identity] = mention.mention_index
        return self
