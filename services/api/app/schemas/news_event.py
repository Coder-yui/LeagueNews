from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class NewsEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    summary: str
    category: str
    entities: list[dict[str, Any]]
    importance_score: float
    credibility: str
    event_type: str
    status: str
    primary_item_id: int | None
    first_published_at: datetime | None
    last_activity_at: datetime | None
    occurred_at: datetime | None
    created_at: datetime
