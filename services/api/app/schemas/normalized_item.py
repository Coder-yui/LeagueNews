from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class NormalizedItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    raw_item_id: int
    normalized_title: str
    normalized_text: str
    summary: str
    category: str
    entities: list[dict[str, Any]]
    content_type: str | None
    primary_topic: str
    secondary_topics: list[str]
    facets: dict[str, Any]
    ontology_version: str
    importance_score: float
    importance_dimensions: dict[str, Any]
    importance_policy_version: str
    importance_calculation: dict[str, Any]
    language: str | None
    source_language: str | None
    target_language: str
    translated_title: str | None
    translated_text: str | None
    translated_content_blocks: list[dict[str, Any]]
    approved_media_extraction_ids: list[int]
    translated_media_extractions: list[dict[str, Any]]
    translation_status: str
    translation_model: str | None
    analysis_model: str
    analysis_version: str
    current_revision: int
    publication_status: str
    withdrawn_at: datetime | None
    withdrawal_reason: str | None
    created_at: datetime
    updated_at: datetime


class PublishedMediaExtractionRead(BaseModel):
    extraction_id: int
    media_asset_id: int
    block_index: int
    storage_path: str | None
    source_url: str | None
    mime_type: str | None
    confidence: float | None
    original_data: dict[str, Any]
    translated_data: dict[str, Any]


class PublishedItemRead(BaseModel):
    id: int
    raw_item_id: int
    title: str
    summary: str
    category: str
    entities: list[dict[str, Any]]
    content_type: str | None
    primary_topic: str
    secondary_topics: list[str]
    facets: dict[str, Any]
    ontology_version: str
    importance_score: float
    importance_dimensions: dict[str, Any]
    importance_policy_version: str
    source_id: int
    source_name: str
    source_base_url: str | None
    source_url: str | None
    author: str | None
    published_at: datetime | None
    original_title: str | None
    original_content_blocks: list[dict[str, Any]]
    source_language: str | None
    translated_title: str | None
    translated_content_blocks: list[dict[str, Any]]
    translation_status: str
    media_extractions: list[PublishedMediaExtractionRead]
    fact_claims: list[dict[str, Any]]
    event_memberships: list[dict[str, Any]]
    created_at: datetime


class PublishedItemPageRead(BaseModel):
    items: list[PublishedItemRead]
    total: int
    topic_options: list[str]
    content_type_options: list[str]
