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
