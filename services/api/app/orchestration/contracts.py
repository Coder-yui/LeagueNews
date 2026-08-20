from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


ITEM_PROCESSING_GRAPH = "item_processing"
ITEM_PROCESSING_GRAPH_VERSION = "v3.0.0-dev2"
ITEM_PROCESSING_STATE_VERSION = 2


class RunMode(StrEnum):
    PRODUCTION = "production"
    SHADOW = "shadow"
    EXPERIMENT = "experiment"


class ReviewMode(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


class ProcessingStage(StrEnum):
    EVIDENCE = "evidence"
    RELEVANCE = "relevance"
    MEDIA = "media"
    TRANSLATION = "translation"
    MESSAGE_ANALYSIS = "message_analysis"
    IMPORTANCE = "importance"
    EVIDENCE_GATE = "evidence_gate"
    PUBLICATION = "publication"


PROCESSING_STAGE_ORDER: tuple[ProcessingStage, ...] = tuple(ProcessingStage)
PROCESSING_STAGE_OUTPUT_KEYS: dict[ProcessingStage, str] = {
    ProcessingStage.EVIDENCE: "evidence",
    ProcessingStage.RELEVANCE: "relevance",
    ProcessingStage.MEDIA: "media",
    ProcessingStage.TRANSLATION: "translation",
    ProcessingStage.MESSAGE_ANALYSIS: "message_analysis",
    ProcessingStage.IMPORTANCE: "importance",
    ProcessingStage.EVIDENCE_GATE: "evidence_gate",
    ProcessingStage.PUBLICATION: "publication",
}


class ItemProcessingRequest(BaseModel):
    workflow_run_id: int = Field(ge=1)
    raw_item_id: int = Field(ge=1)
    raw_item_revision: int = Field(ge=1)
    run_mode: RunMode
    review_mode: ReviewMode = ReviewMode.AUTOMATIC
    batch_id: int | None = Field(default=None, ge=1)
    restart_from_stage: ProcessingStage = ProcessingStage.EVIDENCE
    replay_from_run_id: int | None = Field(default=None, ge=1)
    graph_version: str = ITEM_PROCESSING_GRAPH_VERSION
    state_version: int = ITEM_PROCESSING_STATE_VERSION

    @model_validator(mode="after")
    def validate_batch_scope(self) -> "ItemProcessingRequest":
        if self.run_mode == RunMode.EXPERIMENT and self.batch_id is None:
            raise ValueError("experiment runs require batch_id")
        if self.run_mode != RunMode.EXPERIMENT and self.batch_id is not None:
            raise ValueError("batch_id is only valid for experiment runs")
        is_replay = self.restart_from_stage != ProcessingStage.EVIDENCE
        if is_replay and self.replay_from_run_id is None:
            raise ValueError("restarting after evidence requires replay_from_run_id")
        if not is_replay and self.replay_from_run_id is not None:
            raise ValueError("replay_from_run_id requires a later restart_from_stage")
        if self.replay_from_run_id == self.workflow_run_id:
            raise ValueError("a replay must create a new workflow run")
        if (
            self.restart_from_stage == ProcessingStage.PUBLICATION
            and self.run_mode != RunMode.PRODUCTION
        ):
            raise ValueError("only production runs can restart publication")
        return self

    @property
    def thread_id(self) -> str:
        scope = f"batch:{self.batch_id}" if self.batch_id is not None else "live"
        return (
            f"{ITEM_PROCESSING_GRAPH}:{self.graph_version}:{self.run_mode}:"
            f"{scope}:run:{self.workflow_run_id}:raw:{self.raw_item_id}:"
            f"revision:{self.raw_item_revision}"
        )


class EvidenceSnapshot(BaseModel):
    raw_item_id: int = Field(ge=1)
    raw_item_revision: int = Field(ge=1)
    title: str = ""
    language: str | None = None
    text: str = ""
    media_asset_ids: list[int] = Field(default_factory=list)
    evidence_fingerprint: str = Field(min_length=1)


class RelevanceProposal(BaseModel):
    decision: Literal["relevant", "irrelevant", "uncertain"]
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)
    requires_manual_review: bool = False
    execution_metadata: dict[str, Any] = Field(default_factory=dict)


class MediaProposal(BaseModel):
    requires_ocr: bool = False
    extraction_ids: list[int] = Field(default_factory=list)
    structured_media: list[dict[str, object]] = Field(default_factory=list)
    ocr_corrections: list[dict[str, object]] = Field(default_factory=list)
    requires_manual_review: bool = False


class TranslationProposal(BaseModel):
    status: str
    normalized_text: str = ""
    language: str | None = None
    source_language: str = "unknown"
    target_language: str = "zh-CN"
    translated_title: str = ""
    translated_text: str = ""
    translated_content_blocks: list[dict[str, object]] = Field(default_factory=list)
    translated_media_extractions: list[dict[str, object]] = Field(default_factory=list)
    glossary_term_ids: list[int] = Field(default_factory=list)
    knowledge_rules: list[dict[str, object]] = Field(default_factory=list)
    translation_model: str | None = None
    requires_manual_review: bool = False


class MessageAnalysisProposal(BaseModel):
    title: str
    summary: str
    products: list[str] = Field(min_length=1, max_length=3)
    content_form: str
    entities: list[dict[str, object]] = Field(default_factory=list, max_length=8)
    classification_version: str = "message-taxonomy-v4"
    classification_source: dict[str, object] = Field(default_factory=dict)
    knowledge_rules: list[dict[str, object]] = Field(default_factory=list)
    analysis_model: str | None = None
    execution_metadata: dict[str, Any] = Field(default_factory=dict)
    requires_manual_review: bool = False


class ImportanceProposal(BaseModel):
    message_type: str
    topics: list[str] = Field(min_length=1)
    importance_score: float = Field(ge=0, le=1)
    classification_source: dict[str, object] = Field(default_factory=dict)
    importance_evidence: list[str] = Field(default_factory=list)
    importance_dimensions: dict[str, object] = Field(default_factory=dict)
    importance_policy_version: str = ""
    calculation: dict[str, object] = Field(default_factory=dict)
    priority_score: float = Field(default=0, ge=0, le=1)
    priority_calculation: dict[str, object] = Field(default_factory=dict)
    analysis_model: str | None = None
    execution_metadata: dict[str, Any] = Field(default_factory=dict)
    requires_manual_review: bool = False


class EvidenceGateProposal(BaseModel):
    decision: Literal["accept", "reject"]
    reasons: list[str] = Field(default_factory=list)
    reason_code: str = ""
    evidence_sources: list[str] = Field(default_factory=list)
    meaningful_text_characters: int = Field(default=0, ge=0)
    designer_patch_extraction_count: int = Field(default=0, ge=0)
    requires_manual_review: bool = False


class ReviewDecision(BaseModel):
    action: Literal["approve", "reject"]
    note: str | None = None
    replacement: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_replacement(self) -> "ReviewDecision":
        if self.action == "reject" and self.replacement is not None:
            raise ValueError("a rejected proposal cannot have a replacement")
        return self


class PublicationResult(BaseModel):
    normalized_item_id: int = Field(ge=1)
    normalized_item_revision: int = Field(ge=1)


class BusinessStageCheckpoint(BaseModel):
    workflow_run_id: int = Field(ge=1)
    stage: ProcessingStage
    output_snapshot: dict[str, Any]
    review_decision: ReviewDecision | None = None
    evidence_fingerprint: str = Field(min_length=1)
    upstream_checkpoint_ids: dict[str, int] = Field(default_factory=dict)
    graph_version: str = ITEM_PROCESSING_GRAPH_VERSION
    state_version: int = ITEM_PROCESSING_STATE_VERSION


class CheckpointReceipt(BaseModel):
    checkpoint_id: int = Field(ge=1)
    workflow_run_id: int = Field(ge=1)
    stage: ProcessingStage


class ReplayPrefix(BaseModel):
    source_run_id: int = Field(ge=1)
    restart_from_stage: ProcessingStage
    state: dict[str, Any]
    checkpoint_ids: dict[str, int]
