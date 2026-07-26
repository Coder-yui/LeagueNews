from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReportGenerate(BaseModel):
    report_type: Literal["daily", "weekly", "monthly"]
    period_start: datetime
    period_end: datetime
    timezone: str = "Asia/Shanghai"

    @model_validator(mode="after")
    def validate_period(self) -> "ReportGenerate":
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be later than period_start")
        return self


class ReportReview(BaseModel):
    note: str | None = None
    reason: str | None = Field(default=None, min_length=1)


class GeneratedReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    report_type: str
    timezone: str
    period_start: datetime
    period_end: datetime
    status: str
    title: str
    content: str
    source_event_ids: list[int]
    source_revision_ids: list[int]
    generation_context: dict[str, Any]
    model_name: str
    review_feedback: dict[str, Any]
    created_at: datetime
    approved_at: datetime | None

