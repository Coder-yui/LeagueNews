from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from collections.abc import Iterator, Sequence
from typing import Any, Generic, Literal, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, field_validator

from app.content_blocks import normalize_content_blocks


ContentKind = Literal["article", "post", "thread", "manual"]


class RawItemCandidate(BaseModel):
    """Validated source-agnostic content ready for shared ingestion."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    native_title: str | None
    canonical_url: str | None
    content_kind: ContentKind
    content_blocks: list[dict[str, Any]]
    provenance: dict[str, Any]
    author_name: str | None = None
    language: str | None = None
    published_at: datetime | None = None
    external_id: str | None = None

    @field_validator("content_blocks")
    @classmethod
    def validate_content_blocks(
        cls, value: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        blocks = normalize_content_blocks(value)
        if not blocks:
            raise ValueError("raw item candidate has no content blocks")
        return blocks

    @field_validator(
        "native_title", "canonical_url", "author_name", "language", "external_id"
    )
    @classmethod
    def normalize_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("canonical_url")
    @classmethod
    def require_http_canonical_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("canonical_url must be an absolute HTTP(S) URL")
        return value

    @field_validator("published_at")
    @classmethod
    def require_aware_published_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("published_at must include a timezone")
        return value


@dataclass(frozen=True, slots=True)
class ConnectorSource:
    """Concrete publisher/account consumed by a reusable connector."""

    id: int
    name: str
    connector_type: str
    external_key: str | None
    base_url: str | None
    connector_config: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ConnectorRequest:
    source: ConnectorSource
    limit: int
    since: datetime | None
    options: dict[str, object]
    cursor: dict[str, Any] | None = None


PlatformRecordT = TypeVar("PlatformRecordT")


@dataclass(frozen=True, slots=True)
class FetchBatch(Generic[PlatformRecordT], Sequence[PlatformRecordT]):
    records: list[PlatformRecordT]
    truncated: bool = False
    skipped_ids: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> PlatformRecordT:
        return self.records[index]

    def __iter__(self) -> Iterator[PlatformRecordT]:
        return iter(self.records)


@dataclass(frozen=True, slots=True)
class CandidateBatch(Sequence[RawItemCandidate]):
    items: list[RawItemCandidate]
    truncated: bool
    cursor_used: dict[str, Any]
    next_cursor: dict[str, Any]

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> RawItemCandidate:
        return self.items[index]

    def __iter__(self) -> Iterator[RawItemCandidate]:
        return iter(self.items)


class BaseConnector(ABC, Generic[PlatformRecordT]):
    connector_type: str
    allowed_run_options: frozenset[str] = frozenset()

    async def collect(self, request: ConnectorRequest) -> CandidateBatch:
        """Run the explicit platform-fetch -> canonical-map boundary."""
        if request.source.connector_type != self.connector_type:
            raise ValueError(
                f"{self.connector_type} connector cannot collect "
                f"{request.source.connector_type} source"
            )
        unsupported = set(request.options) - self.allowed_run_options
        if unsupported:
            raise ValueError(
                f"unsupported {self.connector_type} run options: {sorted(unsupported)}"
            )
        fetched = await self.fetch(request)
        records = fetched.records
        truncated = fetched.truncated
        skipped_ids = fetched.skipped_ids
        items = [self.map_record(record) for record in records]
        cursor_used = dict(request.cursor or {})
        return CandidateBatch(
            items=items,
            truncated=truncated,
            cursor_used=cursor_used,
            next_cursor=_advance_cursor(
                cursor_used,
                items,
                truncated=truncated,
                skipped_ids=skipped_ids,
            ),
        )

    @abstractmethod
    async def fetch(
        self, request: ConnectorRequest
    ) -> FetchBatch[PlatformRecordT]:
        """Fetch platform-shaped records without producing RawItems."""

    @abstractmethod
    def map_record(self, record: PlatformRecordT) -> RawItemCandidate:
        """Map one platform-shaped record into the canonical contract."""


def _advance_cursor(
    current: dict[str, Any],
    items: list[RawItemCandidate],
    *,
    truncated: bool,
    skipped_ids: Sequence[str] = (),
) -> dict[str, Any]:
    identifiers = [item.external_id for item in items if item.external_id]
    timestamps = [
        item.published_at.astimezone(UTC)
        for item in items
        if item.published_at is not None
    ]
    pending_ids = {
        str(value)
        for value in current.get("pending_ids", [])
        if isinstance(value, (str, int))
    }
    pending_ids.update(identifiers)
    pending_ids.update(str(value) for value in skipped_ids)
    pending_high = _parse_cursor_time(current.get("pending_high_watermark"))
    if timestamps:
        newest = max(timestamps)
        pending_high = max(pending_high, newest) if pending_high else newest
    watermark = _parse_cursor_time(current.get("watermark"))
    if truncated:
        return {
            "version": 1,
            "watermark": watermark.isoformat() if watermark else None,
            "pending_high_watermark": (
                pending_high.isoformat() if pending_high else None
            ),
            "pending_ids": sorted(pending_ids),
        }
    promoted = max(
        [value for value in (watermark, pending_high, *timestamps) if value is not None],
        default=None,
    )
    return {
        "version": 1,
        "watermark": promoted.isoformat() if promoted else None,
        "pending_high_watermark": None,
        "pending_ids": [],
    }


def _parse_cursor_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.utcoffset() is not None else None
