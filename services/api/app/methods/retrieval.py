"""Runtime retrieval port. No sessions or provider-specific objects cross it."""

from datetime import UTC, datetime
from typing import Protocol

from pydantic import Field, field_validator

from app.methods.contracts import FrozenMethodInput


class EventRetrievalQuery(FrozenMethodInput):
    title: str = ""
    content: str = ""
    summary: str = ""
    products: tuple[str, ...] = ()
    published_at: datetime
    visible_until: datetime
    possible_families: tuple[str, ...] = ()
    entity_hints: dict[str, object] = Field(default_factory=dict)
    window_days: int = Field(default=60, gt=0)
    limit: int = Field(default=12, ge=1, le=24)

    @field_validator("published_at", "visible_until")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def message(self) -> dict[str, object]:
        return {
            "title": self.title,
            "content": self.content,
            "summary": self.summary,
            "products": list(self.products),
            "published_at": self.published_at.isoformat(),
        }


class RetrievedEvent(FrozenMethodInput):
    event_id: int = Field(ge=1)
    revision: int = Field(ge=1)
    event_family: str
    products: tuple[str, ...]
    canonical_anchors: dict[str, object] = Field(default_factory=dict)
    title: str = ""
    current_summary: str = ""
    latest_development: str = ""
    key_facts: tuple[dict[str, object], ...] = ()
    lifecycle_status: str = "developing"
    last_seen_at: str | None = None
    recall_score: float | None = None
    recall_reasons: tuple[str, ...] = ()
    retrieval_source: str = "rules"


class EventRetriever(Protocol):
    async def retrieve(self, query: EventRetrievalQuery) -> tuple[RetrievedEvent, ...]: ...
