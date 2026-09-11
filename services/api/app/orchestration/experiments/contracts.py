import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExperimentTarget(StrEnum):
    MESSAGE_ANALYSIS = "message_analysis"
    IMPORTANCE_SCORING = "importance_scoring"
    ITEM_PROCESSING = "item_processing"
    MESSAGE_IMPORTANCE = "message_importance"
    EVENT_AGGREGATION = "event_aggregation"
    EVENT_IMPORTANCE = "event_importance"
    FEATURED_SELECTION = "featured_selection"
    DAILY_REPORT = "daily_report"
    END_TO_END = "end_to_end"


class ExperimentShape(StrEnum):
    INDEPENDENT = "independent"
    STATEFUL_SCENARIO = "stateful_scenario"
    CANDIDATE_COLLECTION = "candidate_collection"


class LabelSource(StrEnum):
    HUMAN_CONFIRMED = "human_confirmed"
    MODEL_PREFILL = "model_prefill"
    SYNTHETIC_FIXTURE = "synthetic_fixture"
    UNLABELED = "unlabeled"


class FrozenMediaArtifact(BaseModel):
    """A content-addressed media reference included in a frozen dataset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(min_length=1, max_length=200)
    sha256: str = Field(min_length=64, max_length=64)
    uri: str = Field(min_length=1, max_length=2000)
    mime_type: str | None = None
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    byte_size: int | None = Field(default=None, ge=0)


class ScenarioStep(BaseModel):
    """One ordered, answer-free input in a stateful scenario."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1, max_length=160)
    input: dict[str, Any]
    occurred_at: str | None = None
    received_at: str | None = None
    visible_until: str | None = None


class DatasetLabelView(BaseModel):
    """Labels are stored separately from execution input and never sent to executors."""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any] = Field(default_factory=dict)
    source: LabelSource
    schema_version: str = Field(min_length=1, max_length=80)
    evidence_basis: list[str] = Field(default_factory=list)
    ambiguity: list[str] = Field(default_factory=list)


class ExperimentCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    input: dict[str, Any]
    expected: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)
    shape: ExperimentShape | None = None
    split: Literal["train", "validation", "test", "holdout", "unassigned"] = "unassigned"
    group_id: str | None = Field(default=None, min_length=1, max_length=160)
    occurred_at: str | None = None
    received_at: str | None = None
    visible_until: str | None = None
    visible_data_scope: list[str] = Field(default_factory=list)
    media_artifacts: list[FrozenMediaArtifact] = Field(default_factory=list)
    initial_state: dict[str, Any] = Field(default_factory=dict)
    steps: list[ScenarioStep] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    labels: DatasetLabelView | None = None

    @model_validator(mode="after")
    def normalize_label_view(self) -> "ExperimentCase":
        if self.labels is None:
            source = (
                LabelSource.SYNTHETIC_FIXTURE
                if self.expected is not None
                else LabelSource.UNLABELED
            )
            values = dict(self.expected or {})
            self.labels = DatasetLabelView(
                values=values,
                source=source,
                schema_version="legacy-expected-v1",
            )
        elif self.expected is not None and self.labels.values != self.expected:
            raise ValueError("expected and labels.values disagree")
        return self

    def for_execution(self) -> "ExperimentCase":
        """Return a copy with all answer/label fields removed from execution."""

        for value in (self.input, self.initial_state, self.candidates, [step.input for step in self.steps]):
            _reject_embedded_labels(value)
        return ExperimentCase.model_validate(
            {
                "case_id": self.case_id,
                "input": json.loads(json.dumps(self.input, ensure_ascii=False)),
                "tags": [],
                "shape": self.shape,
                "split": "unassigned",
                "group_id": None,
                "occurred_at": self.occurred_at,
                "received_at": self.received_at,
                "visible_until": self.visible_until,
                "visible_data_scope": list(self.visible_data_scope),
                "media_artifacts": [artifact.model_dump(mode="json") for artifact in self.media_artifacts],
                "initial_state": json.loads(json.dumps(self.initial_state, ensure_ascii=False)),
                "steps": [step.model_dump(mode="json") for step in self.steps],
                "candidates": json.loads(json.dumps(self.candidates, ensure_ascii=False)),
                "labels": {
                    "values": {},
                    "source": LabelSource.UNLABELED,
                    "schema_version": "execution-input-v1",
                },
            }
        )

    @property
    def label_source(self) -> LabelSource:
        return self.labels.source if self.labels is not None else LabelSource.UNLABELED

    @property
    def label_values(self) -> dict[str, Any]:
        return dict(self.labels.values) if self.labels is not None else {}


class ExperimentDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=80)
    target: ExperimentTarget
    input_schema_version: str = Field(min_length=1, max_length=80)
    cases: list[ExperimentCase] = Field(min_length=1)
    shape: ExperimentShape = ExperimentShape.INDEPENDENT
    dataset_schema_version: str = "frozen-dataset-v1"
    source_description: str = ""
    label_schema_version: str = "labels-v1"
    split_strategy: str = "explicit"
    group_strategy: str = "explicit-or-none"

    @model_validator(mode="after")
    def validate_case_ids(self) -> "ExperimentDataset":
        groups: dict[str, set[str]] = {}
        for case in self.cases:
            if case.group_id:
                groups.setdefault(case.group_id, set()).add(case.split)
        if any(len(splits) > 1 for splits in groups.values()):
            raise ValueError("group_id crosses data splits")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("dataset case_id values must be unique")
        for case in self.cases:
            if case.shape is not None and case.shape != self.shape:
                raise ValueError("case shape must match dataset shape")
            if self.shape == ExperimentShape.STATEFUL_SCENARIO and not case.steps:
                raise ValueError("stateful scenario cases require ordered steps")
            if self.shape == ExperimentShape.CANDIDATE_COLLECTION and not (
                case.candidates
                or isinstance(case.input.get("candidates"), list)
                or (
                    self.target == ExperimentTarget.END_TO_END
                    and isinstance(case.input.get("messages"), list)
                )
            ):
                raise ValueError("candidate collection cases require candidates")
        return self

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @property
    def execution_fingerprint(self) -> str:
        """Fingerprint evidence/candidate inputs without label views."""

        payload = self.model_dump(exclude={"cases"}, mode="json")
        payload["cases"] = [
            case.for_execution().model_dump(mode="json") for case in self.cases
        ]
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


class CandidateSpec(BaseModel):
    candidate_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    target: ExperimentTarget
    graph_name: str | None = Field(default=None, min_length=1, max_length=80)
    graph_version: str | None = Field(default=None, min_length=1, max_length=80)
    state_version: int | None = Field(default=None, ge=1)
    component_versions: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_graph_identity(self) -> "CandidateSpec":
        graph_fields = (self.graph_name, self.graph_version, self.state_version)
        if any(value is not None for value in graph_fields) and not all(
            value is not None for value in graph_fields
        ):
            raise ValueError("graph_name, graph_version and state_version move together")
        return self


class ExperimentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    dataset: ExperimentDataset
    candidates: list[CandidateSpec] = Field(min_length=1)
    max_concurrency: int = Field(default=1, ge=1, le=32)
    case_timeout_seconds: float = Field(default=180, ge=0.01, le=3600)
    cache_enabled: bool = True
    resume_enabled: bool = True
    max_total_calls: int | None = Field(default=None, ge=1)
    max_cost_usd: float | None = Field(default=None, ge=0)
    code_version: str = "workspace"
    prompt_version: str = "unspecified"
    model_version: str = "fixture-or-configured"
    evaluator_version: str = "frozen-v2"

    @model_validator(mode="after")
    def validate_candidates(self) -> "ExperimentPlan":
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate_id values must be unique")
        for candidate in self.candidates:
            if candidate.target != self.dataset.target:
                raise ValueError("candidate target must match dataset target")
        return self


class ExperimentExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    candidate_id: str
    dataset_fingerprint: str = Field(min_length=64, max_length=64)
    run_mode: Literal["experiment"] = "experiment"
    publication_allowed: Literal[False] = False
    input_fingerprint: str = Field(default="", min_length=0, max_length=64)
    candidate_fingerprint: str = Field(default="", min_length=0, max_length=64)
    cache_key: str = ""
    state_isolation_key: str = ""
    code_version: str = "workspace"
    prompt_version: str = "unspecified"
    model_version: str = "fixture-or-configured"


class CaseResult(BaseModel):
    case_id: str
    status: Literal["succeeded", "failed", "invalid"]
    actual: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float | None = Field(default=None, ge=0)
    cache_hit: bool = False
    call_count: int = Field(default=0, ge=0)
    token_count: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    manual_review_required: bool = False


class CandidateResult(BaseModel):
    candidate: CandidateSpec
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    cases: list[CaseResult]
    metrics: dict[str, Any] = Field(default_factory=dict)
    execution_metadata: dict[str, Any] = Field(default_factory=dict)


class ExperimentReport(BaseModel):
    experiment_id: str
    dataset_name: str
    dataset_version: str
    dataset_fingerprint: str
    target: ExperimentTarget
    candidate_results: list[CandidateResult]
    shape: ExperimentShape = ExperimentShape.INDEPENDENT
    generated_at: str | None = None
    run_metadata: dict[str, Any] = Field(default_factory=dict)


def _reject_embedded_labels(value):
    if isinstance(value, dict):
        forbidden = {"expected", "labels", "gold_labels", "tags"} & value.keys()
        if forbidden:
            raise ValueError(f"execution input contains reserved annotation fields: {sorted(forbidden)}")
        for nested in value.values():
            _reject_embedded_labels(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_embedded_labels(nested)
