import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.connectors.base import ConnectorRequest, ConnectorSource
from app.connectors.tencent_lol import TencentConnectorError, TencentLolConnector


FIXTURES = Path(__file__).parent / "fixtures" / "connectors"


def request(limit: int = 1) -> ConnectorRequest:
    return ConnectorRequest(
        source=ConnectorSource(
            id=1,
            name="腾讯英雄联盟",
            connector_type="tencent_lol",
            external_key=None,
            base_url="https://lol.qq.com",
            connector_config={},
        ),
        limit=limit,
        since=None,
        options={},
    )


class FakeJSONClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = iter(responses)

    async def __aenter__(self) -> "FakeJSONClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(self, url: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
        return httpx.Response(
            200,
            json=next(self.responses),
            request=httpx.Request("GET", url),
        )


def load_json(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_tencent_collect_uses_docid_and_preserves_media_order() -> None:
    connector = TencentLolConnector(
        http_client_factory=lambda: FakeJSONClient(
            [load_json("tencent_news_list.json"), load_json("tencent_article.json")]
        )
    )

    items = asyncio.run(connector.collect(request()))

    item = items[0]
    assert item.external_id == "1566318436975419583"
    assert item.canonical_url.endswith("docid=1566318436975419583")
    assert item.native_title == "首届海斗大赛"
    assert item.author_name == "英雄联盟官方"
    assert item.language == "zh-CN"
    assert [block["type"] for block in item.content_blocks] == [
        "paragraph",
        "image",
        "paragraph",
        "embed",
    ]
    assert item.content_blocks[1]["source_url"] == "https://static.gametalk.qq.com/a.png"
    assert "sContent" not in item.provenance["source_response"]
    assert "iComment" not in item.provenance["source_response"]


def test_tencent_rejects_empty_article() -> None:
    payload = load_json("tencent_article.json")
    payload["data"]["result"]["sContent"] = "<div><img src='https://example.com/only.png'></div>"
    discovery = load_json("tencent_news_list.json")["data"]["result"][0]

    with pytest.raises(TencentConnectorError, match="body is empty"):
        TencentLolConnector.parse_article(payload, discovery)


def test_tencent_maps_redirect_article_to_external_link_block() -> None:
    payload = load_json("tencent_article.json")
    result = payload["data"]["result"]
    result["iIsRedirect"] = "1"
    result["sRedirectURL"] = (
        "https://lol.qq.com/act/a20200421weekfree/index.html?siteId=750"
    )
    result["sContent"] = result["sRedirectURL"]
    discovery = load_json("tencent_news_list.json")["data"]["result"][0]

    item = TencentLolConnector.parse_article(payload, discovery)

    assert item.content_blocks == [
        {
            "id": "b0001",
            "type": "embed",
            "embed_kind": "external_link",
            "source_url": result["sRedirectURL"],
            "text": "查看完整公告",
        }
    ]
