"""Serializable inputs, method selection and result contracts."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.importance import (
    AudienceRegion,
    CompetitionRegion,
    ImportanceScale,
    Prominence,
    SkinTier,
)
from app.domain.message_entities import EntityType
from app.domain.message_taxonomy import (
    CLASSIFICATION_VERSION,
    ContentForm as MessageContentForm,
    MessageType,
    Product,
    TOPIC_ORDER,
    Topic,
    message_content_error,
)


class FrozenMethodInput(BaseModel):
    """Serializable method input; it cannot be changed after assembly."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ExtractedEntity(BaseModel):
    name: str = Field(min_length=1)
    type: EntityType
    canonical_name: str | None = None


class MessageContentAnalysisResult(BaseModel):
    """Stable domain output for the message-content analysis method."""

    title: str = Field(default="", max_length=500)
    summary: str
    entities: list[ExtractedEntity] = Field(default_factory=list, max_length=8)
    products: list[Product] = Field(min_length=1, max_length=3)
    content_form: MessageContentForm
    classification_version: Literal[CLASSIFICATION_VERSION] = CLASSIFICATION_VERSION

    @model_validator(mode="after")
    def validate_controlled_classification(self) -> "MessageContentAnalysisResult":
        self.title = self.title.strip()
        if self.content_form in {"media_only", "link_only"}:
            self.summary = ""
            self.entities = []
        error = message_content_error(
            products=list(self.products),
            content_form=self.content_form,
            title=self.title,
            summary=self.summary,
            entities=list(self.entities),
        )
        if error:
            raise ValueError(error)
        return self


class MessageClassificationImportanceResult(BaseModel):
    """Stable domain output for message classification and importance inputs."""

    message_type: MessageType
    topics: list[Topic] = Field(min_length=1)
    scale: ImportanceScale
    audience_region: AudienceRegion
    competition_region: CompetitionRegion
    prominence: Prominence
    skin_tier: SkinTier
    is_bulk_update: bool
    evidence: list[str] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def normalize_topic_order(self) -> "MessageClassificationImportanceResult":
        selected = set(self.topics)
        self.topics = [topic for topic in TOPIC_ORDER if topic in selected]
        return self


class MessageAnalysisInput(FrozenMethodInput):
    title: str = ""
    content: str = ""
    evidence_structure: dict[str, object] = Field(default_factory=dict)
    source_context: dict[str, object] = Field(default_factory=dict)
    knowledge_rules: tuple[str, ...] = ()


class ImportanceScoringInput(FrozenMethodInput):
    content: str = ""
    extracted_facts: dict[str, object] = Field(default_factory=dict)
    products: tuple[str, ...] = ()
    content_form: str
    source_context: dict[str, object] = Field(default_factory=dict)
    knowledge_rules: tuple[str, ...] = ()


class EventAggregationInput(FrozenMethodInput):
    message: dict[str, object] = Field(default_factory=dict)
    possible_event_families: tuple[str, ...] = ()
    candidates: tuple[dict[str, object], ...] = ()


class FeaturedCandidate(FrozenMethodInput):
    normalized_item_id: int = Field(ge=1)
    importance_score: float = Field(ge=0, le=1)
    content_form: str


class FeaturedPlan(FrozenMethodInput):
    selected_item_ids: tuple[int, ...] = ()
    decisions: tuple[dict[str, object], ...] = ()


class DailyReportPlan(FrozenMethodInput):
    sections: dict[str, tuple[dict[str, object], ...]] = Field(default_factory=dict)


class MethodAssemblyConfig(FrozenMethodInput):
    message_analysis: str = "baseline"
    importance_scoring: str = "baseline"
    importance_calculation: str = "baseline"
    event_aggregation: str = "baseline"
    event_recall: str = "baseline"
    featured_selection: str = "threshold"
    daily_report: str = "baseline"
    prompt_refs: dict[str, str] = Field(default_factory=dict)
    prompt_contents: dict[str, str] = Field(default_factory=dict)
    model_parameters: dict[str, dict[str, object]] = Field(default_factory=dict)
    strategy_parameters: dict[str, dict[str, object]] = Field(default_factory=dict)

    def implementation_for(self, method: str) -> str:
        try:
            return {
                "message_analysis": self.message_analysis,
                "importance_scoring": self.importance_scoring,
                "importance_calculation": self.importance_calculation,
                "event_aggregation": self.event_aggregation,
                "event_recall": self.event_recall,
                "featured_selection": self.featured_selection,
                "daily_report": self.daily_report,
            }[method]
        except KeyError as exc:
            raise ValueError(f"unknown method: {method}") from exc


class MethodCallRecord(FrozenMethodInput):
    method: str
    implementation: str
    prompt_ref: str | None = None
    model_parameters: dict[str, object] = Field(default_factory=dict)
    strategy_parameters: dict[str, object] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MethodSelection:
    method: str
    implementation: str
    prompt_ref: str | None
    model_parameters: dict[str, object]
    strategy_parameters: dict[str, object]
    prompt_contents: dict[str, str]
