import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.connectors.base import ConnectorRequest, ConnectorSource
from app.connectors.tencent_lol import (
    TencentConnectorError,
    TencentLolConnector,
    TencentRedirectContent,
)


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
    def __init__(
        self,
        responses: list[dict[str, object] | bytes | tuple[int, dict[str, str]]],
    ) -> None:
        self.responses = iter(responses)
        self.requested_urls: list[str] = []

    async def __aenter__(self) -> "FakeJSONClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> httpx.Response:
        self.requested_urls.append(url)
        payload = next(self.responses)
        if isinstance(payload, tuple):
            status_code, response_headers = payload
            return httpx.Response(
                status_code,
                headers=response_headers,
                request=httpx.Request("GET", url),
            )
        kwargs = {"json": payload} if isinstance(payload, dict) else {"content": payload}
        return httpx.Response(200, request=httpx.Request("GET", url), **kwargs)


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


def test_tencent_collect_skips_deleted_detail_and_advances_cursor() -> None:
    list_payload = load_json("tencent_news_list.json")
    first = list_payload["data"]["result"][0]
    first["iDocID"] = "deleted-doc"
    second = dict(first)
    second["iDocID"] = "live-doc"
    list_payload["data"]["result"] = [first, second, dict(second, iDocID="later-doc")]
    article_payload = load_json("tencent_article.json")
    article_payload["data"]["result"]["iDocID"] = "live-doc"
    connector = TencentLolConnector(
        http_client_factory=lambda: FakeJSONClient(
            [
                list_payload,
                {"status": 0, "msg": "news not found"},
                article_payload,
            ]
        )
    )

    batch = asyncio.run(connector.collect(request(limit=1)))

    assert len(batch) == 1
    assert batch[0].external_id == "live-doc"
    assert batch.truncated is True
    assert "deleted-doc" in batch.next_cursor["pending_ids"]


def test_tencent_rejects_empty_article() -> None:
    payload = load_json("tencent_article.json")
    payload["data"]["result"]["sContent"] = "<div><img src='https://example.com/only.png'></div>"
    discovery = load_json("tencent_news_list.json")["data"]["result"][0]

    with pytest.raises(TencentConnectorError, match="body is empty"):
        TencentLolConnector.parse_article(payload, discovery)


def test_tencent_collect_fetches_and_decodes_official_redirect_article() -> None:
    payload = load_json("tencent_article.json")
    result = payload["data"]["result"]
    result["iIsRedirect"] = "1"
    result["sRedirectURL"] = "https://lol.qq.com/gicp/news/410/example.html"
    result["sContent"] = result["sRedirectURL"]
    html = (
        "<html><head><meta charset='gbk'></head><body>"
        "<div class='article'><p>完整的停机更新公告正文。</p>"
        "<img src='/images/notice.png'></div></body></html>"
    ).encode("gb18030")
    client = FakeJSONClient(
        [load_json("tencent_news_list.json"), payload, html]
    )
    connector = TencentLolConnector(http_client_factory=lambda: client)

    item = asyncio.run(connector.collect(request()))[0]

    assert item.canonical_url == result["sRedirectURL"]
    assert item.content_blocks[0]["text"] == "完整的停机更新公告正文。"
    assert item.content_blocks[1]["source_url"] == "https://lol.qq.com/images/notice.png"
    assert client.requested_urls[-1] == result["sRedirectURL"]
    assert item.provenance["redirect_response"] == {
        "extraction_kind": "html_article",
        "url": result["sRedirectURL"],
        "content_length": len(html),
    }


def test_tencent_requires_fetched_content_for_official_redirect() -> None:
    payload = load_json("tencent_article.json")
    result = payload["data"]["result"]
    result["iIsRedirect"] = "1"
    result["sRedirectURL"] = "https://lol.qq.com/gicp/news/410/example.html"
    discovery = load_json("tencent_news_list.json")["data"]["result"][0]

    with pytest.raises(TencentConnectorError, match="redirect content is missing"):
        TencentLolConnector.parse_article(payload, discovery)


def test_tencent_does_not_follow_http_redirect_from_trusted_page() -> None:
    payload = load_json("tencent_article.json")
    result = payload["data"]["result"]
    result["iIsRedirect"] = "1"
    result["sRedirectURL"] = "https://lol.qq.com/gicp/news/410/example.html"
    client = FakeJSONClient(
        [
            load_json("tencent_news_list.json"),
            payload,
            (302, {"location": "https://example.com/outside"}),
        ]
    )
    connector = TencentLolConnector(http_client_factory=lambda: client)

    with pytest.raises(TencentConnectorError, match="returned another redirect"):
        asyncio.run(connector.collect(request()))

    assert "https://example.com/outside" not in client.requested_urls


def test_tencent_does_not_fetch_unsupported_official_page() -> None:
    payload = load_json("tencent_article.json")
    result = payload["data"]["result"]
    result["iIsRedirect"] = "1"
    result["sRedirectURL"] = "https://lol.qq.com/act/unknown/index.html"
    client = FakeJSONClient([load_json("tencent_news_list.json"), payload])
    connector = TencentLolConnector(http_client_factory=lambda: client)

    with pytest.raises(TencentConnectorError, match="page is unsupported"):
        asyncio.run(connector.collect(request()))

    assert client.requested_urls == [
        "https://apps.game.qq.com/cmc/zmMcnTargetContentList"
        "?r0=json&page=1&num=50&target=24&source=web_pc",
        "https://apps.game.qq.com/cmc/zmMcnContentInfo"
        "?type=0&docid=1566318436975419583&source=web_pc",
    ]


def test_tencent_maps_external_redirect_to_link_without_fetching() -> None:
    payload = load_json("tencent_article.json")
    result = payload["data"]["result"]
    result["iIsRedirect"] = "1"
    result["sRedirectURL"] = "https://example.com/announcement"
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
    assert item.canonical_url == result["sRedirectURL"]


def test_tencent_maps_week_free_cms_issue_to_structured_blocks() -> None:
    payload = load_json("tencent_article.json")
    result = payload["data"]["result"]
    result["iIsRedirect"] = "1"
    result["sRedirectURL"] = (
        "https://lol.qq.com/act/a20200421weekfree/index.html?siteId=750"
    )
    discovery = load_json("tencent_news_list.json")["data"]["result"][0]
    board = {
        "record": {
            "freeHero": "1,2",
            "newBulle": {"freeNum": "750", "iDate": "7月24日", "version": "16.14"},
        }
    }
    redirect = TencentRedirectContent(
        url=result["sRedirectURL"],
        html="<html></html>",
        content_length=13,
        week_free_board=f"return {json.dumps(board)};}});",
        hero_list={
            "hero": [
                {"heroId": "1", "name": "黑暗之女"},
                {"heroId": "2", "name": "狂战士"},
            ]
        },
    )

    item = TencentLolConnector.parse_article(payload, discovery, redirect=redirect)

    assert item.content_blocks == [
        {"id": "b0001", "type": "heading", "text": "第750期周免英雄", "level": 2},
        {"id": "b0002", "type": "paragraph", "text": "周免日期：7月24日"},
        {"id": "b0003", "type": "paragraph", "text": "游戏版本：16.14"},
        {
            "id": "b0004",
            "type": "list",
            "items": ["黑暗之女", "狂战士"],
            "ordered": False,
        },
    ]
    assert item.provenance["redirect_response"]["site_id"] == "750"
