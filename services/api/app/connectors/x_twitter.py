import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.connectors.base import (
    BaseConnector,
    ConnectorRequest,
    RawItemCandidate,
)
from app.connectors.web_content import clean_text
from app.core.config import settings


class XConnectorConfigurationError(RuntimeError):
    """The free X connector has no usable local cookie account."""


class XConnectorCollectionError(RuntimeError):
    """X blocked or rejected the configured Web session."""


class XTwitterConnector(BaseConnector[object]):
    connector_type = "x_twitter"

    def __init__(self, *, api_factory: Callable[..., object] | None = None) -> None:
        self.api_factory = api_factory

    async def fetch(self, request: ConnectorRequest) -> list[object]:
        limit = min(
            max(request.limit, 1),
            settings.x_fetch_limit,
            10,
        )
        since = request.since
        source = request.source
        if source.connector_type != self.connector_type:
            raise XConnectorConfigurationError("X connector requires a concrete x_twitter source")
        username = clean_text(source.external_key).lstrip("@")
        if not username:
            raise XConnectorConfigurationError(
                f"X source {source.id} has no external_key username"
            )
        cookie_path = settings.resolved_x_cookie_file
        cookie_string = _load_cookie_string(cookie_path)
        run_dir = settings.project_root / ".run"
        run_dir.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix="twscrape-", suffix=".db", dir=run_dir
        )
        os.close(file_descriptor)
        db_path = Path(temporary_name)

        try:
            api = self._make_api(db_path)
            account_name = "lol_daily_intel_cookie"
            await api.pool.delete_accounts(account_name)
            await api.pool.add_account_cookies(account_name, cookie_string)
            user = await api.user_by_login(username)
            if user is None:
                raise XConnectorCollectionError(f"X user was not found: @{username}")
            records: list[object] = []
            async for tweet in api.user_tweets(user.id, limit=limit):
                published_at = _attr(tweet, "date")
                if isinstance(since, datetime) and isinstance(published_at, datetime):
                    if published_at < since:
                        continue
                records.append(tweet)
            return sorted(
                records,
                key=lambda record: (
                    _attr(record, "date")
                    if isinstance(_attr(record, "date"), datetime)
                    else datetime.min.replace(tzinfo=UTC)
                ),
                reverse=True,
            )[:limit]
        except XConnectorConfigurationError:
            raise
        except Exception as exc:
            raise XConnectorCollectionError(
                "X collection failed; the cookie may be expired, rate-limited, or blocked "
                f"({type(exc).__name__})"
            ) from exc
        finally:
            for suffix in ("", "-shm", "-wal"):
                Path(f"{db_path}{suffix}").unlink(missing_ok=True)

    def map_record(self, record: object) -> RawItemCandidate:
        return self.map_tweet(record)

    def _make_api(self, db_path: Path) -> object:
        if self.api_factory:
            return self.api_factory(
                str(db_path),
                raise_when_no_account=True,
                wait_timeout=15,
                wait_interval=1,
            )
        from twscrape import API

        return API(
            str(db_path),
            raise_when_no_account=True,
            wait_timeout=15,
            wait_interval=1,
        )

    @staticmethod
    def map_tweet(tweet: object) -> RawItemCandidate:
        raw = _object_dict(tweet)
        tweet_id = clean_text(_attr(tweet, "id_str", "id"))
        user = _attr(tweet, "user")
        username = clean_text(_attr(user, "username"))
        display_name = clean_text(_attr(user, "displayname"))
        text = clean_text(_attr(tweet, "rawContent"))
        url = clean_text(_attr(tweet, "url")) or f"https://x.com/{username}/status/{tweet_id}"
        published_at = _attr(tweet, "date")
        media = _attr(tweet, "media")
        blocks: list[dict[str, object]] = []
        if text:
            blocks.append({"type": "paragraph", "text": text})
        for photo in _as_list(_attr(media, "photos")):
            photo_url = clean_text(_attr(photo, "url"))
            if photo_url:
                blocks.append(
                    {
                        "type": "image",
                        "source_url": photo_url,
                        "mime_type": "image/jpeg",
                        "alt_text": clean_text(
                            _attr(photo, "altText", "alt_text", "alt")
                        )
                        or None,
                        "caption": None,
                    }
                )
        # Video files and their thumbnails are intentionally excluded from the
        # ingestion contract. The original post URL remains available to users.
        if not text:
            raise XConnectorCollectionError(f"X tweet has no text: {tweet_id or 'unknown'}")
        quoted = _attr(tweet, "quotedTweet")
        quoted_url = clean_text(_attr(quoted, "url")) or None
        if quoted_url:
            blocks.append(
                {
                    "type": "embed",
                    "embed_kind": "quoted_post",
                    "source_url": quoted_url,
                    "text": "引用推文",
                }
            )
        if _as_list(_attr(media, "videos")):
            blocks.append(
                {
                    "type": "embed",
                    "embed_kind": "video",
                    "source_url": url,
                    "text": "视频",
                }
            )
        return RawItemCandidate(
            external_id=tweet_id,
            native_title=None,
            canonical_url=url,
            content_kind="post",
            author_name=display_name or None,
            language=clean_text(_attr(tweet, "lang")) or None,
            published_at=published_at if isinstance(published_at, datetime) else None,
            content_blocks=blocks,
            provenance={
                "source_response": _sanitize_tweet_payload(raw),
                "quoted_tweet_id": clean_text(_attr(quoted, "id_str", "id")) or None,
                "quoted_tweet_url": quoted_url,
            },
        )


def _load_cookie_string(path: Path) -> str:
    if not path.is_file():
        raise XConnectorConfigurationError(
            f"X cookie file is missing: {path}; export auth_token and ct0 from a dedicated account"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise XConnectorConfigurationError(f"X cookie file is invalid JSON: {path}") from exc
    if isinstance(payload, list):
        cookies = {
            clean_text(item.get("name")): clean_text(item.get("value"))
            for item in payload
            if isinstance(item, dict) and item.get("name")
        }
    elif isinstance(payload, dict):
        cookies = {
            clean_text(key): clean_text(value)
            for key, value in payload.items()
            if isinstance(value, (str, int))
        }
    else:
        cookies = {}
    if not cookies.get("auth_token") or not cookies.get("ct0"):
        raise XConnectorConfigurationError(
            "X cookie file must contain non-empty auth_token and ct0 cookies"
        )
    return "; ".join(f"{key}={value}" for key, value in cookies.items())


def _attr(value: object, *names: str) -> object:
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if value is not None and hasattr(value, name):
            return getattr(value, name)
    return None


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _object_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    method = getattr(value, "dict", None)
    if callable(method):
        result = method()
        return result if isinstance(result, dict) else {}
    return {}


def _sanitize_tweet_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "id",
        "id_str",
        "url",
        "date",
        "lang",
        "rawContent",
        "conversationId",
        "hashtags",
        "links",
        "media",
        "isQuoteStatus",
        "inReplyToTweetId",
    }
    sanitized = {key: payload[key] for key in allowed if key in payload}
    media = sanitized.get("media")
    if isinstance(media, dict):
        sanitized["media"] = {
            "photos": media.get("photos", []),
        }
    return json.loads(json.dumps(sanitized, default=str))
