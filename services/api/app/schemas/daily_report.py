from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.normalized_item import PublishedItemRead

DailyReportSection = Literal["lolpc", "esports", "tft", "other"]


class DailyReportRead(BaseModel):
    id: int
    report_date: date
    status: str
    sections: dict[DailyReportSection, list[PublishedItemRead]]
    created_at: datetime
    updated_at: datetime
