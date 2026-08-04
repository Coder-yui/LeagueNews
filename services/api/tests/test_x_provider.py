import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest

from app.connectors.base import ConnectorRequest, ConnectorSource
from app.connectors.x_twitter import XConnectorConfigurationError, XTwitterConnector
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


def test_x_cursor_scans_past_already_ingested_capped_batch(
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

        async def user_tweets(self, _uid: int, *, limit: int):
            for tweet in records[:limit]:
                yield tweet

    connector = XTwitterConnector(api_factory=lambda *_args, **_kwargs: API())
    first = asyncio.run(
        connector.collect(
            ConnectorRequest(
                source=x_source(), limit=2, since=None, options={}, cursor={}
            )
        )
    )
    assert first.truncated is True
    assert [item.external_id for item in first] == [
        "1945200000000000000",
        "1945199999999999999",
    ]

    second = asyncio.run(
        connector.collect(
            ConnectorRequest(
                source=x_source(),
                limit=2,
                since=None,
                options={},
                cursor=first.next_cursor,
            )
        )
    )
    assert [item.external_id for item in second] == [
        "1945199999999999998",
        "1945199999999999997",
    ]
    assert set(first.next_cursor["pending_ids"]).isdisjoint(
        item.external_id for item in second
    )
