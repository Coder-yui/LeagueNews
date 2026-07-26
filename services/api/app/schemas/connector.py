from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConnectorRunRequest(BaseModel):
    source_id: int | None = None
    limit: int = Field(default=10, ge=1, le=50)
    since: datetime | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("since")
    @classmethod
    def require_aware_since(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("since must include a timezone")
        return value

    @model_validator(mode="after")
    def reject_reserved_options(self) -> "ConnectorRunRequest":
        reserved = {"limit", "since", "source", "source_id"}
        overlap = reserved.intersection(self.options)
        if overlap:
            raise ValueError(f"options contains reserved keys: {sorted(overlap)}")
        return self


class ConnectorRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    connector_type: str
    status: str
    discovered_count: int
    created_count: int
    revised_count: int
    skipped_count: int
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None


class ConnectorRegistrationRead(BaseModel):
    connector_type: str
