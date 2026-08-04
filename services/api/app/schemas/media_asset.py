from datetime import datetime

from pydantic import BaseModel, ConfigDict
from typing import Any


class MediaAssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    raw_item_id: int
    block_index: int
    source_url: str | None
    storage_path: str | None
    public_path: str | None
    visibility: str
    published_at: datetime | None
    mime_type: str | None
    sha256: str | None
    width: int | None
    height: int | None
    alt_text: str | None
    caption: str | None
    ocr_text: str | None
    created_at: datetime


class MediaExtractionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    media_asset_id: int
    task_type: str
    provider: str
    ocr_engine: str
    structuring_model: str
    schema_version: str
    status: str
    raw_ocr_text: str
    ocr_lines: list[dict[str, Any]]
    structured_data: dict[str, Any]
    processing_config: dict[str, Any]
    confidence: float | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
