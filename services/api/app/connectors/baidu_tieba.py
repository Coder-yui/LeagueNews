from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.connectors.base import (
    BaseConnector,
    ConnectorRequest,
    FetchBatch,
    RawItemCandidate,
)
from app.connectors.web_content import clean_text


class BaiduTiebaConnectorConfigurationError(RuntimeError):
    """A concrete Tieba account or forum scope is missing."""


class BaiduTiebaConnectorCollectionError(RuntimeError):
    """Tieba rejected a request or returned incomplete data."""


@dataclass(frozen=True, slots=True)
class TiebaThreadRecord:
    thread: object
    posts: list[object]
    expected_user_id: int
    expected_forum: str


class BaiduTiebaConnector(BaseConnector[TiebaThreadRecord]):
    connector_type = "baidu_tieba"

    def __init__(self, *, client_factory: Callable[[], object] | None = None) -> None:
        self.client_factory = client_factory

    async def fetch(
        self, request: ConnectorRequest
    ) -> FetchBatch[TiebaThreadRecord]:
        source = request.source
        if source.connector_type != self.connector_type:
            raise BaiduTiebaConnectorConfigurationError(
                "Baidu Tieba connector requires a concrete baidu_tieba source"
            )
        user_id_text = clean_text(source.external_key)
        if not user_id_text.isdecimal():
            raise BaiduTiebaConnectorConfigurationError(
                f"Tieba source {source.id} external_key must be a numeric user ID"
            )
        user_id = int(user_id_text)
        forum_name = clean_text(source.connector_config.get("forum_name"))
        if not forum_name:
            raise BaiduTiebaConnectorConfigurationError(
                f"Tieba source {source.id} connector_config requires forum_name"
            )
        limit = min(max(request.limit, 1), 10)
        pending_ids = {
            str(value)
            for value in (request.cursor or {}).get("pending_ids", [])
        }
        since = request.since
        max_thread_pages = min(
            max(int(source.connector_config.get("max_thread_pages", 5)), 1), 20
        )
        max_post_pages = min(
            max(int(source.connector_config.get("max_post_pages", 100)), 1), 200
        )

        try:
            async with self._make_client() as client:
                scan_limit = limit + len(pending_ids) + 1
                threads = await self._discover_threads(
                    client,
                    user_id=user_id,
                    forum_name=forum_name,
                    limit=scan_limit,
                    max_pages=max_thread_pages,
                )
                records: list[TiebaThreadRecord] = []
                for thread in threads:
                    thread_id = str(int(_attr(thread, "tid") or 0))
                    if thread_id in pending_ids:
                        continue
                    published_at = _timestamp(_attr(thread, "create_time"))
                    if isinstance(since, datetime) and published_at:
                        if published_at < since:
                            continue
                    if len(records) >= limit:
                        return FetchBatch(records=records, truncated=True)
                    posts = await self._fetch_author_posts(
                        client,
                        tid=int(_attr(thread, "tid") or 0),
                        user_id=user_id,
                        max_pages=max_post_pages,
                    )
                    records.append(
                        TiebaThreadRecord(
                            thread=thread,
                            posts=posts,
                            expected_user_id=user_id,
                            expected_forum=forum_name,
                        )
                    )
                return FetchBatch(
                    records=records,
                    # If every discovered thread was already in the cursor,
                    # there is no remaining work even when the scan cap was hit.
                    truncated=bool(records) and len(threads) >= scan_limit,
                )
        except (
            BaiduTiebaConnectorConfigurationError,
            BaiduTiebaConnectorCollectionError,
        ):
            raise
        except Exception as exc:
            raise BaiduTiebaConnectorCollectionError(
                f"Baidu Tieba collection failed ({type(exc).__name__})"
            ) from exc

    def map_record(self, record: TiebaThreadRecord) -> RawItemCandidate:
        return self.map_thread(
            record.thread,
            posts=record.posts,
            expected_user_id=record.expected_user_id,
            expected_forum=record.expected_forum,
        )

    def _make_client(self) -> object:
        if self.client_factory:
            return self.client_factory()
        import aiotieba

        return aiotieba.Client()

    async def _discover_threads(
        self,
        client: object,
        *,
        user_id: int,
        forum_name: str,
        limit: int,
        max_pages: int,
    ) -> list[object]:
        matched: list[object] = []
        seen: set[int] = set()
        for page in range(1, max_pages + 1):
            result = await client.get_user_threads(user_id, page)
            _raise_tieba_error(result, operation="get_user_threads")
            page_threads = list(result)
            for thread in page_threads:
                tid = int(_attr(thread, "tid") or 0)
                author_id = int(_attr(_attr(thread, "user"), "user_id") or 0)
                if (
                    tid
                    and tid not in seen
                    and author_id == user_id
                    and clean_text(_attr(thread, "fname")).casefold()
                    == forum_name.casefold()
                ):
                    seen.add(tid)
                    matched.append(thread)
                    if len(matched) >= limit:
                        return matched
            if len(page_threads) < 60:
                break
        return matched

    async def _fetch_author_posts(
        self,
        client: object,
        *,
        tid: int,
        user_id: int,
        max_pages: int,
    ) -> list[object]:
        if not tid:
            raise BaiduTiebaConnectorCollectionError("Tieba thread has no tid")
        collected: list[object] = []
        seen: set[int] = set()
        for page in range(1, max_pages + 1):
            result = await client.get_posts(
                tid,
                page,
                rn=30,
                only_thread_author=True,
                with_comments=False,
            )
            _raise_tieba_error(result, operation=f"get_posts({tid})")
            for post in result:
                pid = int(_attr(post, "pid") or 0)
                author_id = int(
                    _attr(post, "author_id")
                    or _attr(_attr(post, "user"), "user_id")
                    or 0
                )
                if pid and pid not in seen and author_id == user_id:
                    seen.add(pid)
                    collected.append(post)
            if not bool(_attr(result, "has_more")):
                break
        else:
            raise BaiduTiebaConnectorCollectionError(
                f"Tieba thread {tid} exceeded max_post_pages={max_pages}"
            )
        if not collected:
            raise BaiduTiebaConnectorCollectionError(
                f"Tieba thread {tid} returned no posts by user {user_id}"
            )
        return sorted(
            collected,
            key=lambda post: (
                int(_attr(post, "floor") or 0),
                int(_attr(post, "create_time") or 0),
                int(_attr(post, "pid") or 0),
            ),
        )

    @staticmethod
    def map_thread(
        thread: object,
        *,
        posts: list[object],
        expected_user_id: int,
        expected_forum: str,
        ) -> RawItemCandidate:
        tid = int(_attr(thread, "tid") or 0)
        title = clean_text(_attr(thread, "title"))
        user = _attr(thread, "user")
        author_id = int(_attr(user, "user_id") or 0)
        forum_name = clean_text(_attr(thread, "fname"))
        author = clean_text(
            _attr(user, "show_name")
            or _attr(user, "nick_name_new")
            or _attr(user, "user_name")
        )
        if (
            not tid
            or author_id != expected_user_id
            or forum_name.casefold() != expected_forum.casefold()
        ):
            raise BaiduTiebaConnectorCollectionError(
                "Tieba thread identity does not match the concrete source"
            )

        blocks: list[dict[str, Any]] = []
        post_payloads: list[dict[str, Any]] = []
        plain_posts: list[str] = []
        for post in posts:
            pid = int(_attr(post, "pid") or 0)
            floor = int(_attr(post, "floor") or 0)
            created_at = _timestamp(_attr(post, "create_time"))
            label = f"第{floor}楼" if floor else f"回复 {pid}"
            if created_at:
                label += (
                    f" · {created_at.astimezone(ZoneInfo('Asia/Shanghai')):%Y-%m-%d %H:%M:%S}"
                )
            blocks.append({"type": "heading", "text": label})
            post_blocks = _content_blocks(_attr(post, "contents"))
            blocks.extend(post_blocks)
            post_text = clean_text(_attr(post, "text"))
            if post_text:
                plain_posts.append(post_text)
            post_payloads.append(
                {
                    "pid": pid,
                    "floor": floor,
                    "create_time": int(_attr(post, "create_time") or 0),
                    "reply_num": int(_attr(post, "reply_num") or 0),
                    "agree": int(_attr(post, "agree") or 0),
                    "url": f"https://tieba.baidu.com/p/{tid}?pid={pid}#{pid}",
                }
            )

        if not any(block.get("text") for block in blocks):
            raise BaiduTiebaConnectorCollectionError(f"Tieba thread {tid} has no usable content")
        published_at = _timestamp(_attr(thread, "create_time")) or _timestamp(
            _attr(posts[0], "create_time")
        )
        return RawItemCandidate(
            external_id=str(tid),
            native_title=title or None,
            canonical_url=f"https://tieba.baidu.com/p/{tid}",
            content_kind="thread",
            author_name=author or None,
            language="zh-CN",
            published_at=published_at,
            content_blocks=blocks,
            provenance={
                "forum_name": forum_name,
                "source_user_id": expected_user_id,
                "thread": {
                    "tid": tid,
                    "title": title,
                    "create_time": int(_attr(thread, "create_time") or 0),
                    "reply_num": int(_attr(thread, "reply_num") or 0),
                    "view_num": int(_attr(thread, "view_num") or 0),
                },
                "author_posts": post_payloads,
            },
        )


def _content_blocks(contents: object) -> list[dict[str, Any]]:
    fragments = list(_attr(contents, "objs") or [])
    blocks: list[dict[str, Any]] = []
    text_parts: list[str] = []

    def flush_text() -> None:
        text = clean_text("".join(text_parts))
        text_parts.clear()
        if text:
            blocks.append({"type": "paragraph", "text": text})

    for fragment in fragments:
        origin_url = clean_text(
            _attr(fragment, "origin_src")
            or _attr(fragment, "big_src")
        )
        if origin_url:
            flush_text()
            blocks.append(
                {
                    "type": "image",
                    "source_url": origin_url,
                    "mime_type": _image_mime(origin_url),
                    "alt_text": None,
                    "caption": None,
                }
            )
            continue

        class_name = type(fragment).__name__.casefold()
        if "video" in class_name:
            video_url = clean_text(_attr(fragment, "src"))
            if video_url:
                flush_text()
                blocks.append(
                    {
                        "type": "embed",
                        "embed_kind": "video",
                        "text": "视频",
                        "source_url": video_url,
                    }
                )
            continue

        link_value = _attr(fragment, "url")
        link_url = clean_text(link_value)
        if link_url:
            label = clean_text(
                _attr(fragment, "title") or _attr(fragment, "text")
            ) or "网页链接"
            flush_text()
            blocks.append(
                {
                    "type": "embed",
                    "embed_kind": "external_link",
                    "text": label,
                    "source_url": link_url,
                }
            )
            continue

        text = _attr(fragment, "text")
        if text:
            text_parts.append(str(text))
            continue
        description = clean_text(_attr(fragment, "desc"))
        if description:
            text_parts.append(description)
    flush_text()
    return blocks


def _raise_tieba_error(result: object, *, operation: str) -> None:
    error = _attr(result, "err")
    if error:
        raise BaiduTiebaConnectorCollectionError(f"Tieba {operation} failed: {error}")


def _attr(value: object, name: str) -> object:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None) if value is not None else None


def _timestamp(value: object) -> datetime | None:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC) if timestamp > 0 else None


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
