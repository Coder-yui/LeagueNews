from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    field_validator,
    model_validator,
)

TEXT_BLOCK_TYPES = frozenset({"heading", "paragraph", "list", "quote"})
EMBED_KINDS = frozenset(
    {"video", "poll", "quoted_post", "external_link", "iframe", "audio", "other"}
)


class _BlockBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None


class HeadingBlock(_BlockBase):
    type: Literal["heading"]
    text: str = Field(min_length=1)
    level: int = Field(default=2, ge=1, le=6)


class ParagraphBlock(_BlockBase):
    type: Literal["paragraph"]
    text: str = Field(min_length=1)


class ListBlock(_BlockBase):
    type: Literal["list"]
    items: list[str] = Field(min_length=1)
    ordered: bool = False

    @field_validator("items")
    @classmethod
    def validate_items(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("list block requires at least one non-empty item")
        return cleaned


class QuoteBlock(_BlockBase):
    type: Literal["quote"]
    text: str = Field(min_length=1)
    author: str | None = None
    source_url: HttpUrl | None = None


class ImageBlock(_BlockBase):
    type: Literal["image"]
    source_url: HttpUrl | None = None
    storage_path: str | None = None
    alt_text: str | None = None
    caption: str | None = None
    mime_type: str | None = None

    @model_validator(mode="after")
    def require_location(self) -> "ImageBlock":
        if not self.source_url and not self.storage_path:
            raise ValueError("image block requires source_url or storage_path")
        return self


class EmbedBlock(_BlockBase):
    type: Literal["embed"]
    embed_kind: Literal[
        "video", "poll", "quoted_post", "external_link", "iframe", "audio", "other"
    ] = "other"
    source_url: HttpUrl
    text: str | None = None
    caption: str | None = None


ContentBlock = Annotated[
    HeadingBlock | ParagraphBlock | ListBlock | QuoteBlock | ImageBlock | EmbedBlock,
    Field(discriminator="type"),
]
CONTENT_BLOCK_LIST_ADAPTER = TypeAdapter(list[ContentBlock])


def normalize_content_blocks(
    blocks: list[dict[str, Any] | ContentBlock],
) -> list[dict[str, Any]]:
    """Validate and return canonical ContentBlock v2 data with stable block IDs."""
    normalized: list[dict[str, Any]] = []
    for index, source in enumerate(blocks, start=1):
        if isinstance(source, BaseModel):
            source = source.model_dump(mode="json", exclude_none=True)
        if not isinstance(source, dict):
            raise ValueError(f"content block {index} must be an object")
        block = {key: value for key, value in source.items() if value is not None}
        block_type = str(block.get("type") or "").strip().casefold()
        if block_type == "video":
            block_type = "embed"
            block.setdefault("embed_kind", "video")
        if block_type not in TEXT_BLOCK_TYPES | {"image", "embed"}:
            raise ValueError(f"unsupported content block type: {block_type or '<empty>'}")

        block["id"] = f"b{index:04d}"
        block["type"] = block_type
        if block_type == "heading":
            text = str(block.get("text") or "").strip()
            if not text:
                raise ValueError("heading block requires text")
            block["text"] = text
            block["level"] = min(max(int(block.get("level", 2)), 1), 6)
        elif block_type == "list":
            items = block.get("items")
            if isinstance(items, list):
                clean_items = [str(item).strip() for item in items if str(item).strip()]
            else:
                text = str(block.get("text") or "").strip()
                clean_items = [text] if text else []
            if not clean_items:
                raise ValueError("list block requires at least one item")
            block["items"] = clean_items
            block["ordered"] = bool(block.get("ordered", False))
            block.pop("text", None)
        elif block_type in {"paragraph", "quote"}:
            text = str(block.get("text") or "").strip()
            if not text:
                raise ValueError(f"{block_type} block requires text")
            block["text"] = text
        elif block_type == "image":
            if not (block.get("source_url") or block.get("storage_path")):
                raise ValueError("image block requires source_url or storage_path")
        elif block_type == "embed":
            block["embed_kind"] = _embed_kind(block.get("embed_kind"))
            if not _is_http_url(block.get("source_url")):
                raise ValueError("embed block requires an HTTP(S) source_url")
        normalized.append(block)
    validated = CONTENT_BLOCK_LIST_ADAPTER.validate_python(normalized)
    return [
        block.model_dump(mode="json", exclude_none=True)
        for block in validated
    ]


def text_from_content_blocks(blocks: list[dict[str, Any]]) -> str:
    """Derive the current analysis/translation text view from canonical blocks."""
    parts: list[str] = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == "list":
            items = block.get("items")
            if isinstance(items, list):
                value = "\n".join(str(item) for item in items if str(item).strip())
                if value:
                    parts.append(value)
        elif block_type in TEXT_BLOCK_TYPES:
            value = str(block.get("text") or "").strip()
            if value:
                parts.append(value)
    return "\n\n".join(parts)


def first_text_line(blocks: list[dict[str, Any]]) -> str:
    for line in text_from_content_blocks(blocks).splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            return cleaned
    return ""


def display_title(
    *,
    native_title: str | None,
    author_name: str | None,
    source_name: str | None,
    blocks: list[dict[str, Any]],
    max_length: int = 500,
) -> str | None:
    if native_title and native_title.strip():
        value = native_title.strip()
    else:
        name = (author_name or source_name or "").strip()
        first_line = first_text_line(blocks)
        value = f"{name}：{first_line}" if name and first_line else name or first_line
    if not value:
        return None
    return value if len(value) <= max_length else f"{value[: max_length - 1].rstrip()}…"


def content_hash(blocks: list[dict[str, Any]]) -> str:
    """Hash source-semantic content, excluding local materialization metadata."""
    semantic_blocks = []
    for block in normalize_content_blocks(blocks):
        semantic = {
            key: value
            for key, value in block.items()
            if key not in {"storage_path", "mime_type"}
        }
        semantic_blocks.append(semantic)
    canonical = json.dumps(
        semantic_blocks, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def has_quoted_post(blocks: list[dict[str, Any]]) -> bool:
    return any(
        block.get("type") == "embed" and block.get("embed_kind") == "quoted_post"
        for block in blocks
    )


def _embed_kind(value: object) -> str:
    kind = str(value or "other").strip().casefold()
    return kind if kind in EMBED_KINDS else "other"


def _is_http_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
