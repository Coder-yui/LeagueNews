from datetime import datetime

from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from app.connectors.config import validate_connector_config, validate_external_key


class SourceCreate(BaseModel):
    name: str
    connector_type: str = "manual"
    external_key: str | None = None
    base_url: HttpUrl | None = None
    connector_config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    is_official: bool = False
    reliability_score: float = Field(default=0.5, ge=0, le=1)

    @field_validator("external_key")
    @classmethod
    def normalize_external_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lstrip("@").casefold()
        return normalized or None

    @model_validator(mode="after")
    def validate_connector_fields(self) -> "SourceCreate":
        self.external_key = validate_external_key(
            self.connector_type, self.external_key
        )
        self.connector_config = validate_connector_config(
            self.connector_type, self.connector_config
        )
        return self


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    connector_type: str
    external_key: str | None
    base_url: str | None
    connector_config: dict[str, Any]
    is_active: bool
    is_official: bool
    reliability_score: float
    created_at: datetime


class SourceReliabilityUpdate(BaseModel):
    is_official: bool
    reliability_score: float = Field(ge=0, le=1)
