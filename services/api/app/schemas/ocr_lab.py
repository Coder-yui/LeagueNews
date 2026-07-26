from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OCRParameters(BaseModel):
    scale: float = Field(default=1.0, ge=1.0, le=4.0)
    grayscale: bool = False
    contrast: float = Field(default=1.0, ge=0.5, le=3.0)
    sharpness: float = Field(default=1.0, ge=0.5, le=3.0)
    text_score: float | None = Field(default=None, ge=0, le=1)
    box_thresh: float | None = Field(default=None, ge=0, le=1)
    unclip_ratio: float | None = Field(default=None, ge=0.5, le=3.0)
    use_cls: bool = True
    divider_x_ratio: float | None = Field(default=None, ge=0.1, le=0.4)
    line_brightness: int = Field(default=105, ge=40, le=220)
    line_coverage: float = Field(default=0.82, ge=0.5, le=1.0)


class OCRTestRequest(BaseModel):
    media_asset_id: int
    profile_name: str = Field(default="custom", min_length=1, max_length=120)
    parameters: OCRParameters = Field(default_factory=OCRParameters)


class OCRAssetRead(BaseModel):
    media_asset_id: int
    raw_item_id: int
    raw_title: str | None
    published_at: datetime | None
    block_index: int
    storage_path: str
    source_url: str | None
    width: int | None
    height: int | None


class OCRTestRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    media_asset_id: int
    profile_name: str
    parameters: dict[str, Any]
    status: str
    raw_text: str
    lines: list[dict[str, Any]]
    confidence: float
    source_width: int
    source_height: int
    processed_width: int
    processed_height: int
    overlay_path: str | None
    table_overlay_path: str | None = None
    table_data: dict[str, Any] = Field(default_factory=dict)
    structure_confidence: float | None = None
    engine: str
    created_at: datetime


class OCRProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    parameters: dict[str, Any]
    source_test_run_id: int | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
