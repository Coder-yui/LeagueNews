"""Serializable inputs, method selection and result contracts."""

from dataclasses import dataclass
from pydantic import BaseModel, ConfigDict, Field


class FrozenMethodInput(BaseModel):
    """Serializable method input; it cannot be changed after assembly."""

    model_config = ConfigDict(frozen=True, extra="forbid")


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
