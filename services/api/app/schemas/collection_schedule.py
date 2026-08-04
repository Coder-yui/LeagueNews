from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CollectionScheduleUpdate(BaseModel):
    enabled: bool = False
    interval_minutes: int = Field(default=60, ge=5, le=10080)
    retry_delay_minutes: int = Field(default=15, ge=1, le=1440)
    fetch_limit: int = Field(default=10, ge=1, le=50)
    overlap_minutes: int = Field(default=10, ge=0, le=1440)
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_reserved_options(self) -> "CollectionScheduleUpdate":
        reserved = {"limit", "since", "source", "source_id"}
        overlap = reserved.intersection(self.options)
        if overlap:
            raise ValueError(f"options contains reserved keys: {sorted(overlap)}")
        return self


class CollectionScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    source_name: str
    connector_type: str
    enabled: bool
    interval_minutes: int
    retry_delay_minutes: int
    fetch_limit: int
    overlap_minutes: int
    options: dict[str, Any]
    collection_cursor: dict[str, Any]
    next_run_at: datetime | None
    run_requested_at: datetime | None
    last_started_at: datetime | None
    last_finished_at: datetime | None
    last_success_at: datetime | None
    last_connector_run_id: int | None
    last_status: str
    last_error: str | None
    consecutive_failures: int
    lease_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
