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
