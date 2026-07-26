from datetime import datetime
from typing import Any

from pydantic import BaseModel


class EventFeedItem(BaseModel):
    normalized_item_id: int
    raw_item_id: int
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
    media_extractions: list["EventMediaExtraction"]


class EventMediaExtraction(BaseModel):
    media_asset_id: int
    storage_path: str | None
    task_type: str
    status: str
    confidence: float | None
    structured_data: dict[str, Any]


class EventFeedRead(BaseModel):
    id: int
    title: str
    summary: str
    category: str
    entities: list[dict[str, Any]]
    importance_score: float
    credibility: str
    occurred_at: datetime | None
    created_at: datetime
    items: list[EventFeedItem]
