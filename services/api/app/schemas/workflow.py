from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.event_workflow import EventReviewTaskRead


class ProcessingRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    raw_item_id: int
    supersedes_run_id: int | None
    workflow_type: str
    status: str
    outcome: str | None
    current_stage: str
    execution_mode: str
    correction_id: int | None
    restart_from_stage: str | None
    context: dict[str, Any]
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class ReviewTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    processing_run_id: int
    stage: str
    status: str
    proposal: dict[str, Any]
    feedback: dict[str, Any]
    decision_source: str
    policy_version: str | None
    created_at: datetime
    resolved_at: datetime | None


class OCRReviewExtractionRead(BaseModel):
    id: int
    media_asset_id: int
    block_index: int
    source_url: str | None
    storage_path: str | None
    confidence: float | None
    raw_ocr_text: str
    table_data: dict[str, Any]


class OCRWorkflowReviewRead(BaseModel):
    review_id: int
    processing_run_id: int
    raw_item_id: int
    raw_title: str | None
    canonical_url: str | None
    status: str
    corrections: list[dict[str, Any]]
    extractions: list[OCRReviewExtractionRead]
    created_at: datetime


class ReviewQueueItemRead(BaseModel):
    raw_item_id: int
    raw_title: str | None
    canonical_url: str | None
    source_name: str
    processing_run_id: int | None
    normalized_item_id: int | None
    current_stage: str
    completed_stages: list[str]
    review_kind: Literal["message", "ocr", "event"]
    message_review: ReviewTaskRead | None = None
    ocr_review: OCRWorkflowReviewRead | None = None
    event_review: EventReviewTaskRead | None = None
    created_at: datetime


class ReviewApproval(BaseModel):
    note: str | None = None


class ReviewCorrectionApproval(BaseModel):
    content_type: str | None = None
    importance_score: float | None = Field(default=None, ge=0, le=1)
    note: str | None = None


class OCRTableRecordCorrection(BaseModel):
    target: str = Field(min_length=1, max_length=255)
    raw_changes: list[str] = Field(default_factory=list)
    bbox: list[int] = Field(default_factory=list)
    ocr_confidence: float = Field(default=1.0, ge=0, le=1)


class OCRTableSectionCorrection(BaseModel):
    section_type: Literal[
        "champion_buff",
        "champion_nerf",
        "champion_adjustment",
        "system_buff",
        "system_nerf",
        "system_adjustment",
        "item_buff",
        "item_nerf",
        "item_adjustment",
        "rune_buff",
        "rune_nerf",
        "rune_adjustment",
        "adjustment",
        "other",
    ]
    label: str = Field(min_length=1, max_length=255)
    records: list[OCRTableRecordCorrection] = Field(min_length=1)


class OCRTableCorrection(BaseModel):
    preview_kind: Literal["preview", "full_preview"]
    divider_x: int | None = Field(default=None, ge=0)
    structure_confidence: float = Field(default=1.0, ge=0, le=1)
    sections: list[OCRTableSectionCorrection] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)
    boundaries: list[int] = Field(default_factory=list)


class OCRReviewCorrection(BaseModel):
    extraction_id: int
    table_data: OCRTableCorrection
    note: str | None = None


class GlossaryCorrection(BaseModel):
    source_term: str = Field(min_length=1, max_length=255)
    preferred_translation: str = Field(min_length=1, max_length=255)
    forbidden_translations: list[str] = Field(default_factory=list)
    scope: str = Field(default="lol", min_length=1, max_length=160)
    notes: str | None = None


class ReviewRejection(BaseModel):
    feedback_type: Literal[
        "ocr_error",
        "translation_term",
        "translation_correction",
        "analysis_correction",
    ]
    reason: str | None = Field(default=None, min_length=1)
    corrected_values: dict[str, Any] = Field(default_factory=dict)
    knowledge_rule: str | None = None
    knowledge_scope: str = Field(default="global", min_length=1, max_length=160)
    glossary_updates: list[GlossaryCorrection] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_rejection_content(self) -> "ReviewRejection":
        if self.reason is not None:
            self.reason = self.reason.strip() or None
        if self.feedback_type == "ocr_error":
            return self
        if self.feedback_type in {"translation_term", "translation_correction"}:
            if not self.reason and not self.glossary_updates:
                raise ValueError("翻译退回必须填写退回理由或至少一条术语修正")
        elif not self.reason:
            raise ValueError("该审核阶段必须填写退回理由")
        return self


class KnowledgeRuleCreate(BaseModel):
    knowledge_type: Literal[
        "analysis", "translation", "event_aggregation"
    ]
    scope: str = "global"
    rule_text: str = Field(min_length=1)
    correction_data: dict[str, Any] = Field(default_factory=dict)
    lifecycle_status: Literal["draft", "evaluated", "active", "retired"] = "draft"
    is_active: bool = False


class KnowledgeRuleUpdate(BaseModel):
    knowledge_type: Literal[
        "analysis", "translation", "event_aggregation"
    ] | None = None
    scope: str | None = None
    rule_text: str | None = Field(default=None, min_length=1)
    correction_data: dict[str, Any] | None = None
    is_active: bool | None = None
    lifecycle_status: Literal["draft", "evaluated", "active", "retired"] | None = None
    evaluation_summary: dict[str, Any] | None = None


class KnowledgeRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    knowledge_type: str
    scope: str
    rule_text: str
    correction_data: dict[str, Any]
    source_review_id: int | None
    source_event_review_id: int | None
    version: int
    is_active: bool
    lifecycle_status: str
    evaluation_summary: dict[str, Any]
    evaluated_at: datetime | None
    promoted_at: datetime | None
    retired_at: datetime | None
    created_at: datetime
    updated_at: datetime


class GlossaryTermCreate(GlossaryCorrection):
    is_active: bool = True


class GlossaryTermUpdate(BaseModel):
    source_term: str | None = Field(default=None, min_length=1, max_length=255)
    preferred_translation: str | None = Field(default=None, min_length=1, max_length=255)
    forbidden_translations: list[str] | None = None
    scope: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class GlossaryTermRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_term: str
    preferred_translation: str
    forbidden_translations: list[str]
    scope: str
    notes: str | None
    source_review_id: int | None
    version: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
