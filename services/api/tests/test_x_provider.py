import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest

from app.connectors.base import ConnectorRequest, ConnectorSource
from app.connectors.x_twitter import (
    XConnectorConfigurationError,
    XTwitterConnector,
    _is_authored_or_retweet,
    _tweet_author_user_id,
)
from app.core.config import settings


FIXTURES = Path(__file__).parent / "fixtures" / "connectors"


def x_source(username: str = "riotphroxzon") -> ConnectorSource:
    return ConnectorSource(
        id=2,
        name="Matt Leung-Harrison (@RiotPhroxzon)",
        connector_type="x_twitter",
        external_key=username,
        base_url=f"https://x.com/{username}",
        connector_config={},
    )


def request(source: ConnectorSource | None = None) -> ConnectorRequest:
    return ConnectorRequest(
        source=source or x_source(),
        limit=1,
        since=None,
        options={},
    )


def test_x_maps_text_media_alt_and_quote_without_secrets() -> None:
    tweet = json.loads((FIXTURES / "x_user_tweets.json").read_text(encoding="utf-8"))[0]
    tweet["date"] = datetime.fromisoformat(tweet["date"])
    tweet["rawContent"] = "Gameplay update first line.\nSecond line stays in the body."
    tweet["media"]["videos"] = [
        {
            "thumbnailUrl": "https://pbs.twimg.com/ext_tw_video_thumb/example.jpg",
            "variants": [
                {
                    "bitrate": 832000,
                    "contentType": "video/mp4",
                    "url": "https://video.twimg.com/ext_tw_video/example.mp4",
                }
            ],
        }
    ]

    item = XTwitterConnector.map_tweet(tweet)

    assert item.external_id == "1945200000000000000"
    assert item.author_name == "Matt Leung-Harrison"
    assert item.native_title is None
    assert item.canonical_url.endswith("/status/1945200000000000000")
    assert [block["type"] for block in item.content_blocks] == [
        "paragraph",
        "image",
        "embed",
        "embed",
    ]
    assert item.content_blocks[1]["alt_text"] == "A balance-change chart"
    assert all(block["type"] != "video" for block in item.content_blocks)
    assert "video.twimg.com" not in json.dumps(item.content_blocks)
    assert "video.twimg.com" not in json.dumps(item.provenance)
    assert item.provenance["quoted_tweet_id"] == "1945100000000000000"
    assert "cookies" not in item.provenance["source_response"]
    assert item.content_blocks[2]["embed_kind"] == "quoted_post"
    assert item.content_blocks[3]["embed_kind"] == "video"


def test_x_uses_retweeted_media_and_original_post_for_native_video() -> None:
    tweet = json.loads((FIXTURES / "x_user_tweets.json").read_text(encoding="utf-8"))[0]
    tweet["date"] = datetime.fromisoformat(tweet["date"])
    tweet["rawContent"] = (
        "RT @LCSOfficial: Tune in to https://t.co/QnwJPtPgcx "
        "for the show https://t.co/vueipJHjCs"
    )
    tweet["links"] = [
        {
            "url": "http://lolesports.com",
            "tcourl": "https://t.co/QnwJPtPgcx",
        }
    ]
    tweet["media"] = {"photos": [], "videos": []}
    tweet["quotedTweet"] = None
    tweet["retweetedTweet"] = {
        "id_str": "2080712956434596217",
        "url": "https://x.com/LCSOfficial/status/2080712956434596217",
        "links": tweet["links"],
        "media": {
            "photos": [{"url": "https://pbs.twimg.com/media/show-card.png"}],
            "videos": [],
        },
    }

    item = XTwitterConnector.map_tweet(tweet)

    assert item.content_blocks[0]["text"] == (
        "RT @LCSOfficial: Tune in to lolesports.com for the show"
    )
    assert item.content_blocks[1]["type"] == "image"
    assert item.content_blocks[1]["mime_type"] == "image/png"
    assert item.content_blocks[2]["source_url"] == "http://lolesports.com/"
    assert all("t.co" not in json.dumps(block) for block in item.content_blocks)
    assert item.provenance["retweeted_tweet_id"] == "2080712956434596217"


def test_x_expands_external_video_link_without_leaving_tco_in_body() -> None:
    tweet = json.loads((FIXTURES / "x_user_tweets.json").read_text(encoding="utf-8"))[0]
    tweet["date"] = datetime.fromisoformat(tweet["date"])
    tweet["rawContent"] = "Patch rundown is out!\n\nhttps://t.co/jEFjKjG8uA"
    tweet["media"] = {"photos": [], "videos": []}
    tweet["quotedTweet"] = None
    tweet["links"] = [
        {
            "url": "https://youtu.be/Uc8lbPoPG1M",
            "text": "youtu.be/Uc8lbPoPG1M",
            "tcourl": "https://t.co/jEFjKjG8uA",
        }
    ]

    item = XTwitterConnector.map_tweet(tweet)

    assert item.content_blocks == [
        {"id": "b0001", "type": "paragraph", "text": "Patch rundown is out!"},
        {
            "id": "b0002",
            "type": "embed",
            "embed_kind": "video",
            "source_url": "https://youtu.be/Uc8lbPoPG1M",
            "text": "外部视频",
        },
    ]


def test_x_removes_photo_attachment_tco_without_creating_duplicate_link() -> None:
    tweet = json.loads((FIXTURES / "x_user_tweets.json").read_text(encoding="utf-8"))[0]
    tweet["date"] = datetime.fromisoformat(tweet["date"])
    tweet["rawContent"] = "Don't miss these showmatches 👀 https://t.co/GBRiKz7hOT"
    tweet["links"] = []
    tweet["quotedTweet"] = None

    item = XTwitterConnector.map_tweet(tweet)

    assert item.content_blocks[0]["text"] == "Don't miss these showmatches 👀"
    assert [block["type"] for block in item.content_blocks] == ["paragraph", "image"]
    assert "t.co" not in json.dumps(item.content_blocks)


def test_x_missing_cookie_is_clear_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(settings, "x_cookie_file", str(missing))
    connector = XTwitterConnector()

    with pytest.raises(XConnectorConfigurationError, match="cookie file is missing"):
        asyncio.run(connector.collect(request()))


def test_x_invalid_cookie_is_clear_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_file = tmp_path / "x-cookies.json"
    cookie_file.write_text('{"auth_token": "only-one"}', encoding="utf-8")
    monkeypatch.setattr(settings, "x_cookie_file", str(cookie_file))
    connector = XTwitterConnector()

    with pytest.raises(XConnectorConfigurationError, match="auth_token and ct0"):
        asyncio.run(connector.collect(request()))


def test_x_rejects_wrong_source_type() -> None:
    connector = XTwitterConnector()
    wrong_source = x_source()
    wrong_source = ConnectorSource(
        id=wrong_source.id,
        name=wrong_source.name,
        connector_type="weibo",
        external_key=wrong_source.external_key,
        base_url=wrong_source.base_url,
        connector_config=wrong_source.connector_config,
    )

    with pytest.raises(ValueError, match="cannot collect weibo source"):
        asyncio.run(connector.collect(request(wrong_source)))


def test_x_cursor_uses_provider_pagination_without_rescanning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_file = tmp_path / "x-cookies.json"
    cookie_file.write_text('{"auth_token":"a","ct0":"b"}', encoding="utf-8")
    monkeypatch.setattr(settings, "x_cookie_file", str(cookie_file))
    fixture = json.loads(
        (FIXTURES / "x_user_tweets.json").read_text(encoding="utf-8")
    )[0]
    records = []
    for offset in range(4):
        tweet = dict(fixture)
        tweet["id_str"] = str(1945200000000000000 - offset)
        tweet["date"] = datetime.fromisoformat(f"2026-07-14T1{8-offset}:00:00+00:00")
        tweet["rawContent"] = f"tweet {offset}"
        tweet["media"] = {"photos": [], "videos": []}
        tweet["quotedTweet"] = None
        records.append(tweet)

    class Pool:
        async def delete_accounts(self, *_: object) -> None:
            return None

        async def add_account_cookies(self, *_: object) -> None:
            return None

    class API:
        pool = Pool()

        async def user_by_login(self, _username: str):
            return type("User", (), {"id": 1})()

    connector = XTwitterConnector(api_factory=lambda *_args, **_kwargs: API())
    requested_cursors: list[str | None] = []

    async def fetch_page(
        _api: object,
        *,
        target_user_id: int,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[object], str | None]:
        assert target_user_id == 1
        assert limit == 2
        requested_cursors.append(cursor)
        return (
            (records[:2], "next-page")
            if cursor is None
            else (records[2:], None)
        )

    monkeypatch.setattr(connector, "_fetch_tweet_page", fetch_page)
    first = asyncio.run(
        connector.collect(
            ConnectorRequest(
                source=x_source(),
                limit=2,
                since=None,
                options={},
                cursor={},
                historical=True,
            )
        )
    )
    assert first.truncated is True
    assert [item.external_id for item in first] == [
        "1945200000000000000",
        "1945199999999999999",
    ]
    assert first.next_cursor == {"version": 2, "x_pagination_cursor": "next-page"}

    second = asyncio.run(
        connector.collect(
            ConnectorRequest(
                source=x_source(),
                limit=2,
                since=None,
                options={},
                cursor=first.next_cursor,
                historical=True,
            )
        )
    )
    assert [item.external_id for item in second] == [
        "1945199999999999998",
        "1945199999999999997",
    ]
    assert second.truncated is False
    assert second.next_cursor == {"version": 2, "x_pagination_cursor": None}
    assert requested_cursors == [None, "next-page"]


def test_x_normal_collection_retains_pending_cursor_strategy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_file = tmp_path / "x-cookies.json"
    cookie_file.write_text('{"auth_token":"a","ct0":"b"}', encoding="utf-8")
    monkeypatch.setattr(settings, "x_cookie_file", str(cookie_file))
    fixture = json.loads(
        (FIXTURES / "x_user_tweets.json").read_text(encoding="utf-8")
    )[0]
    records = []
    for offset in range(3):
        tweet = dict(fixture)
        tweet["id_str"] = str(1945200000000000000 - offset)
        tweet["date"] = datetime.fromisoformat(f"2026-07-14T1{8-offset}:00:00+00:00")
        tweet["rawContent"] = f"tweet {offset}"
        tweet["media"] = {"photos": [], "videos": []}
        tweet["quotedTweet"] = None
        records.append(tweet)

    class Pool:
        async def delete_accounts(self, *_: object) -> None:
            return None

        async def add_account_cookies(self, *_: object) -> None:
            return None

    class API:
        pool = Pool()

        async def user_by_login(self, _username: str):
            return type("User", (), {"id": 1})()

        async def user_tweets(self, _uid: int, *, limit: int):
            for tweet in records[:limit]:
                yield tweet

    connector = XTwitterConnector(api_factory=lambda *_args, **_kwargs: API())
    batch = asyncio.run(
        connector.collect(
            ConnectorRequest(source=x_source(), limit=2, since=None, options={}, cursor={})
        )
    )

    assert batch.truncated is True
    assert "pending_ids" in batch.next_cursor
    assert "x_pagination_cursor" not in batch.next_cursor


# ——————————————————————————————————————————————————————————————————————————————
# Author-attribution filter: keep only the target account's own tweets and
# retweets, discard unrelated recommendations / mentions (串台).
# ——————————————————————————————————————————————————————————————————————————————


def _tweet_dict(*, author_id: object, retweeted: object = None, date: str = "2026-08-10T10:00:00+00:00") -> dict:
    """Factory for a lightweight tweet-shaped dict compatible with _attr."""
    user: dict | None
    if author_id is None:
        user = None
    elif isinstance(author_id, dict):
        user = author_id
    else:
        user = {"id": author_id, "id_str": str(author_id), "username": "u", "displayname": "U"}
    tweet: dict = {
        "id_str": "1",
        "date": datetime.fromisoformat(date),
        "user": user,
        "rawContent": "hello",
        "url": "https://x.com/u/status/1",
    }
    if retweeted is not None:
        tweet["retweetedTweet"] = retweeted
    return tweet


def test_tweet_author_user_id_accepts_id_and_id_str() -> None:
    assert _tweet_author_user_id(_tweet_dict(author_id=42)) == 42
    assert _tweet_author_user_id(
        _tweet_dict(author_id={"id": None, "id_str": "42", "username": "x", "displayname": "X"})
    ) == 42
    assert _tweet_author_user_id(
        _tweet_dict(author_id={"rest_id": "99", "username": "x", "displayname": "X"})
    ) == 99


def test_tweet_author_user_id_returns_none_when_user_missing() -> None:
    assert _tweet_author_user_id(_tweet_dict(author_id=None)) is None
    assert _tweet_author_user_id(
        _tweet_dict(author_id={"username": "no id", "displayname": "no id"})
    ) is None


def test_authorship_filter_keeps_own_tweets() -> None:
    target = 100
    own = _tweet_dict(author_id=target)
    reply = _tweet_dict(author_id=target, date="2026-08-10T11:00:00+00:00")
    # 回复帖作者也是目标账号 → 必须保留
    reply["inReplyToTweetId"] = "9999999"

    assert _is_authored_or_retweet(target, own) is True
    assert _is_authored_or_retweet(target, reply) is True


def test_authorship_filter_keeps_retweets() -> None:
    target = 100
    original_author = 200
    # 转推的 envelope.user 通常是目标账号，这里显式模拟两种情况：
    # 1) retweetedTweet 存在，作者 id 正确（常规）
    rt_normal = _tweet_dict(author_id=target, retweeted={"id_str": "RT1"})
    # 2) retweetedTweet 存在，但 envelope user id 被 parser 弄错了 —— 也应该保留（双保险）
    rt_parser_weird = _tweet_dict(author_id=original_author, retweeted={"id_str": "RT2"})

    assert _is_authored_or_retweet(target, rt_normal) is True
    assert _is_authored_or_retweet(target, rt_parser_weird) is True


def test_authorship_filter_drops_unrelated_recommendations() -> None:
    target = 100
    stranger = 9999
    # 路人独立 tweet：没有 retweetedTweet 标记，作者不是目标 → 丢弃
    unrelated = _tweet_dict(author_id=stranger)
    # 路人 tweet 内容里 @ 了目标账号（mention 帖）→ 同样丢弃
    mention = _tweet_dict(author_id=stranger, date="2026-08-10T12:00:00+00:00")
    mention["rawContent"] = "@target you're wrong"

    assert _is_authored_or_retweet(target, unrelated) is False
    assert _is_authored_or_retweet(target, mention) is False


def test_authorship_filter_preserves_tweets_when_user_field_is_missing() -> None:
    """Conservative fallback: unknown authorship means keep (防误杀)."""
    target = 100
    broken = _tweet_dict(author_id=None)
    assert _is_authored_or_retweet(target, broken) is True


def test_recent_collection_filters_out_stranager_tweets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """混合列表里只保留本人发和转推，串台路人帖要被过滤掉（recent 路径）."""
    cookie_file = tmp_path / "x-cookies.json"
    cookie_file.write_text('{"auth_token":"a","ct0":"b"}', encoding="utf-8")
    monkeypatch.setattr(settings, "x_cookie_file", str(cookie_file))

    TARGET = 42
    STRANGER = 9
    stranger_post = _tweet_dict(author_id=STRANGER, date="2026-08-15T10:00:00+00:00")
    stranger_post["rawContent"] = "recommended unrelated post"
    own_post = _tweet_dict(author_id=TARGET, date="2026-08-15T09:00:00+00:00")
    own_post["id_str"] = "2"
    retweet_post = _tweet_dict(author_id=TARGET, retweeted={"id_str": "RT-1"}, date="2026-08-15T08:00:00+00:00")
    retweet_post["id_str"] = "3"
    mention_post = _tweet_dict(author_id=STRANGER, date="2026-08-15T07:00:00+00:00")
    mention_post["id_str"] = "4"
    mention_post["rawContent"] = "@target hi"
    stranger_related_only = _tweet_dict(author_id=777, date="2026-08-15T06:00:00+00:00")
    stranger_related_only["id_str"] = "5"
    stranger_related_only["rawContent"] = "related by algorithm only"

    class Pool:
        async def delete_accounts(self, *_: object) -> None:
            return None

        async def add_account_cookies(self, *_: object) -> None:
            return None

    class API:
        pool = Pool()

        async def user_by_login(self, _username: str):
            return type("User", (), {"id": TARGET})()

        async def user_tweets(self, _uid: int, *, limit: int):
            for t in [stranger_post, own_post, retweet_post, mention_post, stranger_related_only]:
                yield t

    connector = XTwitterConnector(api_factory=lambda *_args, **_kwargs: API())
    batch = asyncio.run(
        connector.collect(
            ConnectorRequest(source=x_source(), limit=10, since=None, options={}, cursor={})
        )
    )

    external_ids = [item.external_id for item in batch]
    # 只有 own_post / retweet_post 应该留下来（3 条路人 + 1 条 mention + 1 条算法推荐 都去掉）
    assert external_ids == ["2", "3"]
    assert len(external_ids) == 2
