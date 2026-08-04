from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

from selectolax.parser import HTMLParser

from app.connectors.base import (
    BaseConnector,
    ConnectorRequest,
    FetchBatch,
    RawItemCandidate,
)
from app.connectors.web_content import clean_text
from app.services.weibo_browser import WeiboBrowserError, WeiboBrowserSession


class WeiboConnectorConfigurationError(RuntimeError):
    """The dedicated Weibo browser profile is missing a usable login."""


class WeiboConnectorCollectionError(RuntimeError):
    """Weibo rejected a request or returned an unexpected response."""


@dataclass(frozen=True, slots=True)
class WeiboStatusRecord:
    mblog: dict[str, Any]
    full_text: str | None
    source_uid: str


class WeiboConnector(BaseConnector[WeiboStatusRecord]):
    connector_type = "weibo"
    timeline_endpoint = "https://weibo.com/ajax/statuses/searchProfile"
    long_text_endpoint = "https://weibo.com/ajax/statuses/longtext"

    def __init__(
        self,
        *,
        browser_session_factory: Callable[[], object] = WeiboBrowserSession,
    ) -> None:
        self.browser_session_factory = browser_session_factory

    async def fetch(
        self, request: ConnectorRequest
    ) -> FetchBatch[WeiboStatusRecord]:
        source = request.source
        if source.connector_type != self.connector_type:
            raise WeiboConnectorConfigurationError(
                "Weibo connector requires a concrete weibo source"
            )
        uid = clean_text(source.external_key)
        if not uid.isdecimal():
            raise WeiboConnectorConfigurationError(
                f"Weibo source {source.id} external_key must be a numeric UID"
            )
        limit = min(max(request.limit, 1), 10)
        since = request.since
        include_reposts = bool(source.connector_config.get("include_reposts", True))

        try:
            async with self.browser_session_factory() as browser:
                await browser.open_weibo(f"https://weibo.com/u/{uid}")
                records: list[WeiboStatusRecord] = []
                seen: set[str] = set()
                pending_ids = {
                    str(value)
                    for value in (request.cursor or {}).get("pending_ids", [])
                }
                reached_boundary = False
                exhausted_scan_limit = False
                for page in range(1, 51):
                    payload = _accepted_payload(await browser.get_json(_timeline_url(uid, page)))
                    tweets = _timeline_tweets(payload)
                    for mblog in tweets:
                        status_id = clean_text(mblog.get("mid") or mblog.get("id"))
                        if not status_id or status_id in seen:
                            continue
                        seen.add(status_id)
                        user = mblog.get("user") if isinstance(mblog.get("user"), dict) else {}
                        returned_uid = clean_text(user.get("id") or user.get("idstr"))
                        if returned_uid and returned_uid != uid:
                            continue
                        if not include_reposts and isinstance(
                            mblog.get("retweeted_status"), dict
                        ):
                            continue
                        full_text = None
                        if mblog.get("isLongText") or mblog.get("continue_tag"):
                            full_text = await self._fetch_long_text(
                                browser,
                                clean_text(mblog.get("mblogid") or mblog.get("bid") or status_id),
                            )
                        published_at = _weibo_datetime(mblog.get("created_at"))
                        if isinstance(since, datetime) and published_at:
                            if published_at < since:
                                reached_boundary = True
                                continue
                        if status_id in pending_ids:
                            continue
                        records.append(WeiboStatusRecord(mblog, full_text, uid))
                        if len(records) > limit:
                            return FetchBatch(
                                records=records[:limit],
                                truncated=True,
                            )
                    if not tweets:
                        break
                    if len(tweets) < 20:
                        break
                    if reached_boundary:
                        break
                    if page == 50:
                        exhausted_scan_limit = True
                return FetchBatch(
                    records=records,
                    truncated=exhausted_scan_limit and not reached_boundary,
                )
        except (WeiboConnectorConfigurationError, WeiboConnectorCollectionError):
            raise
        except WeiboBrowserError as exc:
            raise WeiboConnectorCollectionError(str(exc)) from exc
        except Exception as exc:
            raise WeiboConnectorCollectionError(
                f"Weibo collection failed ({type(exc).__name__})"
            ) from exc

    def map_record(self, record: WeiboStatusRecord) -> RawItemCandidate:
        return self.map_mblog(
            record.mblog,
            full_text=record.full_text,
            source_uid=record.source_uid,
        )

    async def _fetch_long_text(self, browser: object, mblogid: str) -> str | None:
        if not mblogid:
            return None
        payload = _accepted_payload(
            await browser.get_json(
                f"{self.long_text_endpoint}?{urlencode({'id': mblogid})}"
            )
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        return clean_text(data.get("longTextContent") or data.get("longText")) or None

    @staticmethod
    def map_mblog(
        mblog: dict[str, Any],
        *,
        full_text: str | None = None,
        source_uid: str | None = None,
    ) -> RawItemCandidate:
        status_id = clean_text(mblog.get("mid") or mblog.get("id") or mblog.get("idstr"))
        bid = clean_text(mblog.get("mblogid") or mblog.get("bid"))
        user = mblog.get("user") if isinstance(mblog.get("user"), dict) else {}
        uid = clean_text(user.get("id") or user.get("idstr") or source_uid)
        author = clean_text(user.get("screen_name"))
        text = _html_text(
            full_text or clean_text(mblog.get("text_raw") or mblog.get("text"))
        )
        text = _remove_attachment_short_urls(text, mblog)
        if not status_id or not uid or not text:
            raise WeiboConnectorCollectionError(
                f"Weibo status is missing id, author, or text: {status_id or 'unknown'}"
            )

        url = f"https://weibo.com/{uid}/{bid or status_id}"
        blocks: list[dict[str, Any]] = [{"type": "paragraph", "text": text}]
        for image_url in _picture_urls(mblog):
            blocks.append(
                {
                    "type": "image",
                    "source_url": image_url,
                    "mime_type": _image_mime(image_url),
                    "alt_text": None,
                    "caption": None,
                }
            )

        quoted_id = None
        quoted_url = None
        retweeted = mblog.get("retweeted_status")
        if isinstance(retweeted, dict):
            quoted_id = clean_text(
                retweeted.get("mid") or retweeted.get("id") or retweeted.get("idstr")
            ) or None
            quoted_user = (
                retweeted.get("user") if isinstance(retweeted.get("user"), dict) else {}
            )
            quoted_uid = clean_text(quoted_user.get("id") or quoted_user.get("idstr"))
            quoted_bid = clean_text(retweeted.get("mblogid") or retweeted.get("bid"))
            quoted_text = _html_text(
                clean_text(retweeted.get("text_raw") or retweeted.get("text"))
            )
            quoted_author = clean_text(quoted_user.get("screen_name"))
            if quoted_text:
                prefix = f"转发自 @{quoted_author}：" if quoted_author else "转发内容："
                blocks.append({"type": "quote", "text": f"{prefix}{quoted_text}"})
            if quoted_uid and (quoted_bid or quoted_id):
                quoted_url = f"https://weibo.com/{quoted_uid}/{quoted_bid or quoted_id}"
                blocks.append(
                    {
                        "type": "embed",
                        "embed_kind": "quoted_post",
                        "text": "原微博",
                        "source_url": quoted_url,
                    }
                )

        for label, link, embed_kind in _attachment_links(mblog, canonical_url=url):
            blocks.append(
                {
                    "type": "embed",
                    "embed_kind": embed_kind,
                    "text": label,
                    "source_url": link,
                }
            )

        return RawItemCandidate(
            external_id=status_id,
            native_title=None,
            canonical_url=url,
            content_kind="post",
            author_name=author or None,
            language="zh-CN",
            published_at=_weibo_datetime(mblog.get("created_at")),
            content_blocks=blocks,
            provenance={
                "source_response": _sanitize_mblog(mblog),
                "quoted_status_id": quoted_id,
                "quoted_status_url": quoted_url,
            },
        )


def _timeline_url(uid: str, page: int) -> str:
    return (
        f"{WeiboConnector.timeline_endpoint}?"
        + urlencode(
            {
                "uid": uid,
                "page": page,
                "hasori": 1,
                "hastext": 1,
                "haspic": 1,
                "hasvideo": 1,
                "hasmusic": 1,
                "hasret": 1,
            }
        )
    )


def _accepted_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("ok") == 1:
        return payload
    if payload.get("ok") == -100 or "login.php" in clean_text(payload.get("url")):
        raise WeiboConnectorConfigurationError(
            "The dedicated Weibo browser profile is not logged in; "
            "run services/api/scripts/setup_weibo_browser.py"
        )
    raise WeiboConnectorCollectionError(
        f"Weibo API rejected the request: {clean_text(payload.get('msg')) or 'unknown error'}"
    )


def _timeline_tweets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    tweets = data.get("list") if isinstance(data, dict) else None
    if not isinstance(tweets, list):
        raise WeiboConnectorCollectionError("Weibo timeline response has no data.list")
    return [tweet for tweet in tweets if isinstance(tweet, dict)]


def _html_text(value: str) -> str:
    if not value:
        return ""
    root = HTMLParser(f"<div>{value}</div>").css_first("div")
    if root is None:
        return clean_text(value)
    lines = [clean_text(line) for line in root.text(separator="\n").splitlines()]
    return "\n".join(line for line in lines if line)


def _picture_urls(mblog: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for picture in _as_dict_list(mblog.get("pics")):
        large = picture.get("large") if isinstance(picture.get("large"), dict) else {}
        image_url = clean_text(large.get("url") or picture.get("url"))
        if image_url:
            urls.append(image_url)
    for pic_id in mblog.get("pic_ids", []) if isinstance(mblog.get("pic_ids"), list) else []:
        clean_id = clean_text(pic_id)
        if clean_id:
            urls.append(f"https://wx1.sinaimg.cn/orj960/{clean_id}")
    return list(dict.fromkeys(urls))


def _attachment_links(
    mblog: dict[str, Any], *, canonical_url: str
) -> list[tuple[str, str, str]]:
    links: list[tuple[str, str, str]] = []
    url_entries = _as_dict_list(mblog.get("url_struct"))
    has_http_struct_link = any(
        _is_http_url(clean_text(entry.get("long_url") or entry.get("url_long")))
        for entry in url_entries
    )
    page_info = mblog.get("page_info")
    object_type = ""
    if isinstance(page_info, dict):
        object_type = clean_text(page_info.get("object_type") or page_info.get("type"))
        link = clean_text(page_info.get("page_url"))
        label = clean_text(
            page_info.get("page_title") or page_info.get("content2") or object_type
        )
        if _is_http_url(link):
            links.append(
                (
                    label or "附加内容",
                    link,
                    _weibo_embed_kind(object_type),
                )
            )
        elif not has_http_struct_link and object_type.casefold() in {
            "video",
            "live",
            "vote",
            "poll",
            "hudongvote",
        }:
            links.append(
                (
                    label or "媒体内容",
                    canonical_url,
                    _weibo_embed_kind(object_type),
                )
            )
    for entry in url_entries:
        link = clean_text(entry.get("long_url") or entry.get("url_long"))
        if _is_http_url(link):
            links.append(
                (
                    clean_text(entry.get("url_title")) or "网页链接",
                    link,
                    _weibo_link_embed_kind(
                        link,
                        label=clean_text(entry.get("url_title")),
                        page_object_type=object_type,
                    ),
                )
            )
    deduplicated: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for label, link, embed_kind in links:
        if link not in seen:
            seen.add(link)
            deduplicated.append((label, link, embed_kind))
    return deduplicated


def _weibo_embed_kind(object_type: str) -> str:
    value = object_type.casefold()
    if value in {"video", "live"}:
        return "video"
    if value in {"vote", "poll", "hudongvote"}:
        return "poll"
    return "external_link"


def _weibo_link_embed_kind(
    url: str,
    *,
    label: str,
    page_object_type: str,
) -> str:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold()
    searchable = f"{hostname}{parsed.path} {label}".casefold()
    if "video.weibo.com" in hostname or "wblive" in searchable or "直播" in label:
        return "video"
    if "vote.weibo.com" in hostname or "投票" in label:
        return "poll"
    return _weibo_embed_kind(page_object_type)


def _remove_attachment_short_urls(text: str, mblog: dict[str, Any]) -> str:
    short_urls = {
        clean_text(entry.get("short_url"))
        for entry in _as_dict_list(mblog.get("url_struct"))
        if _is_http_url(clean_text(entry.get("short_url")))
    }
    cleaned = text
    for short_url in short_urls:
        cleaned = cleaned.replace(short_url, "")
    return clean_text(cleaned)


def _is_http_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _weibo_datetime(value: object) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _as_dict_list(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _image_mime(url: str) -> str | None:
    path = url.lower().split("?", 1)[0]
    if path.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if path.endswith(".png"):
        return "image/png"
    if path.endswith(".gif"):
        return "image/gif"
    if path.endswith(".webp"):
        return "image/webp"
    return None


def _sanitize_mblog(mblog: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "mid",
        "id",
        "idstr",
        "mblogid",
        "bid",
        "created_at",
        "text_raw",
        "text",
        "isLongText",
        "continue_tag",
        "source",
        "user",
        "pics",
        "pic_ids",
        "page_info",
        "url_struct",
        "reposts_count",
        "comments_count",
        "attitudes_count",
    }
    sanitized = {key: mblog[key] for key in allowed if key in mblog}
    retweeted = mblog.get("retweeted_status")
    if isinstance(retweeted, dict):
        sanitized["retweeted_status"] = {
            key: retweeted[key]
            for key in (
                "mid",
                "id",
                "idstr",
                "mblogid",
                "bid",
                "text_raw",
                "text",
                "user",
                "page_info",
            )
            if key in retweeted
        }
    import json

    return json.loads(json.dumps(sanitized, default=str))
