import asyncio
import json
from pathlib import Path

import pytest

from app.connectors.base import ConnectorRequest, ConnectorSource
from app.connectors.weibo import (
    WeiboConnector,
    WeiboConnectorConfigurationError,
)


FIXTURES = Path(__file__).parent / "fixtures" / "connectors"


class FakeBrowserSession:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = iter(responses)
        self.opened_urls: list[str] = []

    async def __aenter__(self) -> "FakeBrowserSession":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def open_weibo(self, url: str) -> None:
        self.opened_urls.append(url)

    async def get_json(self, url: str) -> dict[str, object]:
        return next(self.responses)


def load_json(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def weibo_source() -> ConnectorSource:
    return ConnectorSource(
        id=17,
        name="英雄联盟赛事",
        connector_type="weibo",
        external_key="5756404150",
        base_url="https://weibo.com/u/5756404150",
        connector_config={"include_reposts": True},
    )


def request(source: ConnectorSource | None = None) -> ConnectorRequest:
    return ConnectorRequest(
        source=source or weibo_source(),
        limit=1,
        since=None,
        options={},
    )


def test_weibo_collects_long_text_images_repost_and_attachment_links() -> None:
    connector = WeiboConnector(
        browser_session_factory=lambda: FakeBrowserSession(
            [load_json("weibo_timeline.json"), load_json("weibo_long_text.json")]
        )
    )

    items = asyncio.run(connector.collect(request()))

    item = items[0]
    assert item.external_id == "5190000000000001"
    assert item.canonical_url == "https://weibo.com/5756404150/PxExample"
    assert item.native_title is None
    assert item.author_name == "英雄联盟赛事"
    assert [block["type"] for block in item.content_blocks] == [
        "paragraph",
        "image",
        "quote",
        "embed",
        "embed",
        "embed",
    ]
    assert item.content_blocks[1]["source_url"].endswith("/orj960/example.jpg")
    assert item.provenance["quoted_status_id"] == "5189999999999999"
    assert "weibo.com/5720474518/QuotedBid" in json.dumps(
        item.provenance, ensure_ascii=False
    )
    assert "比赛视频" in item.content_blocks[4]["text"]
    assert "微博投票" in item.content_blocks[5]["text"]


def test_weibo_removes_known_short_url_and_preserves_live_embed() -> None:
    mblog = load_json("weibo_timeline.json")["data"]["list"][0]
    mblog["text_raw"] = "今日比赛直播进行中 http://t.cn/AX9lp2or"
    mblog["isLongText"] = False
    mblog["page_info"] = {
        "object_type": "live",
        "page_title": "英雄联盟赛事的微博直播",
        "page_url": "sinaweibo://chatroom?live_id=example",
    }
    mblog["url_struct"] = [
        {
            "short_url": "http://t.cn/AX9lp2or",
            "long_url": "https://weibo.com/l/wblive/p/show/example",
            "url_title": "英雄联盟赛事的微博直播",
        }
    ]
    mblog["retweeted_status"] = None
    mblog["pic_ids"] = []

    item = WeiboConnector.map_mblog(mblog)

    assert item.content_blocks[0]["text"] == "今日比赛直播进行中"
    assert "t.cn" not in json.dumps(item.content_blocks, ensure_ascii=False)
    assert item.content_blocks[-1]["embed_kind"] == "video"
    assert item.content_blocks[-1]["source_url"] == (
        "https://weibo.com/l/wblive/p/show/example"
    )


def test_weibo_login_rejection_is_clear_configuration_error() -> None:
    connector = WeiboConnector(
        browser_session_factory=lambda: FakeBrowserSession(
            [
                {
                    "ok": -100,
                    "url": "https://weibo.com/login.php?url=https://weibo.com/u/5756404150",
                }
            ]
        )
    )

    with pytest.raises(WeiboConnectorConfigurationError, match="not logged in"):
        asyncio.run(connector.collect(request()))


def test_weibo_requires_numeric_uid() -> None:
    source = weibo_source()
    invalid = ConnectorSource(
        id=source.id,
        name=source.name,
        connector_type=source.connector_type,
        external_key="英雄联盟赛事",
        base_url=source.base_url,
        connector_config=source.connector_config,
    )

    with pytest.raises(WeiboConnectorConfigurationError, match="numeric UID"):
        asyncio.run(WeiboConnector().collect(request(invalid)))


def test_weibo_cursor_resumes_capped_timeline_without_duplicates() -> None:
    template = load_json("weibo_timeline.json")["data"]["list"][0]
    statuses = []
    for offset in range(3):
        status = dict(template)
        status["mid"] = str(5190000000000001 - offset)
        status["id"] = status["mid"]
        status["mblogid"] = f"Cursor{offset}"
        status["text_raw"] = f"cursor status {offset}"
        status["isLongText"] = False
        status["pic_ids"] = []
        status["retweeted_status"] = None
        statuses.append(status)
    timeline = {"ok": 1, "data": {"list": statuses}}
    connector = WeiboConnector(
        browser_session_factory=lambda: FakeBrowserSession([timeline])
    )
    first = asyncio.run(
        connector.collect(
            ConnectorRequest(
                source=weibo_source(), limit=2, since=None, options={}, cursor={}
            )
        )
    )
    assert first.truncated is True

    connector = WeiboConnector(
        browser_session_factory=lambda: FakeBrowserSession([timeline])
    )
    second = asyncio.run(
        connector.collect(
            ConnectorRequest(
                source=weibo_source(),
                limit=2,
                since=None,
                options={},
                cursor=first.next_cursor,
            )
        )
    )
    assert second.truncated is False
    assert [item.external_id for item in second] == ["5189999999999999"]
