from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.raw_item import RawItem
from app.models.source import Source

SourceKind = Literal["official", "unofficial", "unknown"]
SourceBasis = Literal["current", "upstream", "unresolved"]


def resolve_classification_source(
    db: Session,
    raw_item: RawItem,
    *,
    content_form: str,
) -> dict[str, object]:
    current_kind: SourceKind = "official" if raw_item.source.is_official else "unofficial"
    if content_form != "repost":
        return {
            "current_source_kind": current_kind,
            "source_kind": current_kind,
            "basis": "current",
            "upstream_source_url": None,
        }

    upstream_url = _upstream_source_url(raw_item)
    upstream_source = _match_source(db, upstream_url) if upstream_url else None
    if upstream_source is None:
        return {
            "current_source_kind": current_kind,
            "source_kind": "unknown",
            "basis": "unresolved",
            "upstream_source_url": upstream_url,
        }
    return {
        "current_source_kind": current_kind,
        "source_kind": "official" if upstream_source.is_official else "unofficial",
        "basis": "upstream",
        "upstream_source_url": upstream_url,
    }


def _upstream_source_url(raw_item: RawItem) -> str | None:
    payload = raw_item.source_payload.payload if raw_item.source_payload else {}
    for key in ("retweeted_tweet_url", "quoted_tweet_url", "quoted_status_url"):
        value = _normal_url(payload.get(key))
        if value:
            return value
    for block in raw_item.content_blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "embed" and block.get("embed_kind") == "quoted_post":
            value = _normal_url(block.get("source_url"))
            if value:
                return value
        if block.get("type") == "quote":
            value = _normal_url(block.get("source_url"))
            if value:
                return value
    return None


def _normal_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
    except ValueError:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _match_source(db: Session, upstream_url: str) -> Source | None:
    identity = _platform_identity(upstream_url)
    sources = list(db.scalars(select(Source).order_by(Source.id)))
    if identity:
        connector_type, external_key = identity
        matches = [
            source
            for source in sources
            if source.connector_type == connector_type
            and (source.external_key or "").casefold() == external_key.casefold()
        ]
        return matches[0] if len(matches) == 1 else None

    parsed = urlsplit(upstream_url)
    hostname = (parsed.hostname or "").casefold().removeprefix("www.")
    matches = []
    for source in sources:
        if not source.base_url or source.connector_type in {
            "x_twitter",
            "weibo",
            "baidu_tieba",
        }:
            continue
        source_host = (urlsplit(source.base_url).hostname or "").casefold().removeprefix("www.")
        if source_host and (hostname == source_host or hostname.endswith(f".{source_host}")):
            matches.append(source)
    return matches[0] if len(matches) == 1 else None


def _platform_identity(url: str) -> tuple[str, str] | None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    parts = [part for part in parsed.path.split("/") if part]
    if host in {"x.com", "twitter.com"} and parts:
        return "x_twitter", parts[0]
    if host == "weibo.com" and parts:
        uid = parts[1] if parts[0] == "u" and len(parts) > 1 else parts[0]
        return "weibo", uid
    return None
