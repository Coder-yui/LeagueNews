from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


PipelineStage = Literal[
    "relevance",
    "image_ocr",
    "translation",
    "fact_classify",
    "importance",
    "claim_gen",
    "event_decision",
]


class PipelineCorrectionCreate(BaseModel):
    restart_from_stage: PipelineStage
    resume_mode: Literal["manual", "automatic"] = "manual"
    reason: str = Field(min_length=1)


class PipelineCorrectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    raw_item_id: int
    normalized_item_id: int | None
    original_event_ids: list[int]
    source_processing_run_id: int | None
    source_event_run_id: int | None
    checkpoint_id: int | None
    restart_from_stage: str
    resume_mode: str
    reason: str
    status: str
    error_message: str | None
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class ProcessingCheckpointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    raw_item_id: int
    normalized_item_id: int | None
    processing_run_id: int | None
    event_aggregation_run_id: int | None
    correction_id: int | None
    stage: str
    output_snapshot: dict
    artifact_references: dict
    knowledge_snapshot: dict
    model_name: str | None
    decision_source: str
    created_at: datetime
    invalidated_at: datetime | None
    invalidation_reason: str | None


class PipelineJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    raw_item_id: int
    correction_id: int | None
    status: str
    current_stage: str
    processing_run_id: int | None
    event_aggregation_run_id: int | None
    last_checkpoint_id: int | None
    attempts: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    updated_at: datetime
    completed_at: datetime | None
    worker_id: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    recovery_count: int
    recovery_provenance: list[dict]
