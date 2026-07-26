from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
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


PlatformRecordT = TypeVar("PlatformRecordT")


class BaseConnector(ABC, Generic[PlatformRecordT]):
    connector_type: str
    allowed_run_options: frozenset[str] = frozenset()

    async def collect(self, request: ConnectorRequest) -> list[RawItemCandidate]:
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
        records = await self.fetch(request)
        return [self.map_record(record) for record in records]

    @abstractmethod
    async def fetch(self, request: ConnectorRequest) -> list[PlatformRecordT]:
        """Fetch platform-shaped records without producing RawItems."""

    @abstractmethod
    def map_record(self, record: PlatformRecordT) -> RawItemCandidate:
        """Map one platform-shaped record into the canonical contract."""
