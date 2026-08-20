import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ExperimentTarget(StrEnum):
    ITEM_PROCESSING = "item_processing"
    MESSAGE_IMPORTANCE = "message_importance"
    EVENT_AGGREGATION = "event_aggregation"
    EVENT_IMPORTANCE = "event_importance"
    DAILY_REPORT = "daily_report"


class ExperimentCase(BaseModel):
    case_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    input: dict[str, Any]
    expected: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)


class ExperimentDataset(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=80)
    target: ExperimentTarget
    input_schema_version: str = Field(min_length=1, max_length=80)
    cases: list[ExperimentCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_ids(self) -> "ExperimentDataset":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("dataset case_id values must be unique")
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
    experiment_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    dataset: ExperimentDataset
    candidates: list[CandidateSpec] = Field(min_length=1)
    max_concurrency: int = Field(default=1, ge=1, le=32)
    case_timeout_seconds: float = Field(default=180, ge=0.01, le=3600)

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
    experiment_id: str
    candidate_id: str
    dataset_fingerprint: str = Field(min_length=64, max_length=64)
    run_mode: Literal["experiment"] = "experiment"
    publication_allowed: Literal[False] = False


class CaseResult(BaseModel):
    case_id: str
    status: Literal["succeeded", "failed"]
    actual: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None


class CandidateResult(BaseModel):
    candidate: CandidateSpec
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    cases: list[CaseResult]
    metrics: dict[str, Any] = Field(default_factory=dict)


class ExperimentReport(BaseModel):
    experiment_id: str
    dataset_name: str
    dataset_version: str
    dataset_fingerprint: str
    target: ExperimentTarget
    candidate_results: list[CandidateResult]
