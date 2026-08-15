from datetime import datetime

from typing import Any

from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator, model_validator

from app.content_blocks import ContentBlock


class RawItemImport(BaseModel):
    source_id: int | None = None
    external_id: str | None = None
    title: str | None = None
    author: str | None = None
    language: str | None = None
    url: HttpUrl | None = None
    content: str | None = None
    content_blocks: list[ContentBlock] | None = None
    raw_payload: dict[str, Any] | None = None
    published_at: datetime | None = None

    @field_validator("published_at")
    @classmethod
    def require_aware_published_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("published_at must include a timezone")
        return value

    @model_validator(mode="after")
    def require_content_or_url(self) -> "RawItemImport":
        if not self.content and not self.url and not self.content_blocks:
            raise ValueError("content, content_blocks, or url is required")
        return self


class RawItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    external_id: str | None
    native_title: str | None
    display_title: str | None
    content_kind: str
    author_name: str | None
    language: str | None
    canonical_url: str | None
    content_blocks: list[dict[str, Any]]
    content_hash: str | None
    content_hash_version: int
    revision: int
    supersedes_raw_item_id: int | None
    processing_status: str
    published_at: datetime | None
    ingested_at: datetime
    source_name: str
    source_connector_type: str
    normalized_item_id: int | None = None
    products: list[str] | None = None
    message_type: str | None = None
    topics: list[str] | None = None
    summary: str | None = None
    importance_score: float | None = None
    current_pipeline_stage: str | None = None
    current_pipeline_job_id: int | None = None
    current_pipeline_job_status: str | None = None
    current_pipeline_job_retry_pending: bool = False
    processing_runs: list["RawItemProcessingRunRead"] = []


class RawItemProcessingRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    outcome: str | None
    current_stage: str
    context: dict[str, Any]
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class RawItemAdminPageRead(BaseModel):
    items: list[RawItemRead]
    total: int
    total_items: int
    status_counts: dict[str, int]
    source_options: list[dict[str, Any]]
    message_type_options: list[str]
