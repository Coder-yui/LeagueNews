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
    importance_score: float
    credibility: str
    credibility_score: float
    credibility_evidence: list[str]
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
    created_at: datetime
    updated_at: datetime
