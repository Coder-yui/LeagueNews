import asyncio
import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from app.connectors.base import ConnectorRequest, ConnectorSource
from app.connectors.riot_official import RiotConnectorError, RiotOfficialConnector


FIXTURES = Path(__file__).parent / "fixtures" / "connectors"


def request(limit: int = 1) -> ConnectorRequest:
    return ConnectorRequest(
        source=ConnectorSource(
            id=1,
            name="Riot Games",
            connector_type="riot_official",
            external_key=None,
            base_url=RiotOfficialConnector.list_url,
            connector_config={},
        ),
        limit=limit,
        since=None,
        options={},
    )


class FakeHTTPClient:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses

    async def __aenter__(self) -> "FakeHTTPClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(self, url: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
        return httpx.Response(200, text=self.responses[url], request=httpx.Request("GET", url))


def test_riot_collect_preserves_order_and_metadata() -> None:
    list_html = (FIXTURES / "riot_news_list.html").read_text(encoding="utf-8")
    article_html = (FIXTURES / "riot_article.html").read_text(encoding="utf-8")
    article_url = "https://www.leagueoflegends.com/en-us/news/game-updates/patch-example/"
    connector = RiotOfficialConnector(
        http_client_factory=lambda: FakeHTTPClient(
            {
                RiotOfficialConnector.list_url: list_html,
                article_url: article_html,
            }
        )
    )

    items = asyncio.run(connector.collect(request()))

    assert len(items) == 1
    item = items[0]
    assert item.native_title == "Patch Example Notes"
    assert item.author_name == "Riot Tester"
    assert item.language == "en"
    assert item.published_at.isoformat() == "2026-07-14T18:00:00+00:00"
    assert [block["type"] for block in item.content_blocks] == [
        "paragraph",
        "image",
        "heading",
        "paragraph",
    ]
    assert item.content_blocks[1].get("alt_text") is None
    assert item.content_blocks[1]["caption"] == "Patch overview"


def test_riot_rejects_changed_list_structure() -> None:
    connector = RiotOfficialConnector(
        http_client_factory=lambda: FakeHTTPClient(
            {RiotOfficialConnector.list_url: "<html><nav>Only navigation</nav></html>"}
        )
    )

    with pytest.raises(RiotConnectorError, match="structure changed"):
        asyncio.run(connector.collect(request()))


def test_riot_prefers_embedded_smart_list_and_skips_external_links() -> None:
    items = [
        {
            "title": "Internal article",
            "action": {
                "payload": {
                    "url": "/en-us/news/community/internal-article",
                }
            },
            "category": {"machineName": "community"},
            "publishedAt": "2026-07-22T17:00:00.000Z",
        },
        {
            "title": "External video",
            "action": {
                "payload": {
                    "url": "https://www.youtube.com/watch?v=example",
                }
            },
            "category": {"machineName": "esports"},
            "publishedAt": "2026-07-21T17:00:00.000Z",
        },
    ]
    payload = {
        "props": {
            "pageProps": {
                "page": {
                    "blades": [
                        {
                            "items": items,
                        }
                    ]
                }
            }
        }
    }
    html = (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}"
        "</script></html>"
    )

    discoveries = RiotOfficialConnector.parse_list(html)

    assert len(discoveries) == 1
    assert discoveries[0]["title"] == "Internal article"
    assert discoveries[0]["category"] == "community"


def test_riot_web_batch_records_boundary_cursor_for_overlap() -> None:
    list_html = (FIXTURES / "riot_news_list.html").read_text(encoding="utf-8")
    article_html = (FIXTURES / "riot_article.html").read_text(encoding="utf-8")
    article_url = "https://www.leagueoflegends.com/en-us/news/game-updates/patch-example/"
    connector = RiotOfficialConnector(
        http_client_factory=lambda: FakeHTTPClient(
            {
                RiotOfficialConnector.list_url: list_html,
                article_url: article_html,
            }
        )
    )
    batch = asyncio.run(
        connector.collect(
            ConnectorRequest(
                source=request().source,
                limit=1,
                since=datetime.fromisoformat("2026-07-14T17:59:59+00:00"),
                options={},
                cursor={
                    "version": 1,
                    "watermark": "2026-07-14T17:59:59+00:00",
                    "pending_ids": [],
                },
            )
        )
    )
    assert batch.truncated is True
    assert batch.cursor_used["watermark"] == "2026-07-14T17:59:59+00:00"
    assert batch.next_cursor["watermark"] == "2026-07-14T17:59:59+00:00"
    assert len(batch.next_cursor["pending_ids"]) == 1

    connector = RiotOfficialConnector(
        http_client_factory=lambda: FakeHTTPClient(
            {
                RiotOfficialConnector.list_url: list_html,
                "https://www.leagueoflegends.com/en-us/news/dev/dev-example/": article_html,
            }
        )
    )
    resumed = asyncio.run(
        connector.collect(
            ConnectorRequest(
                source=request().source,
                limit=1,
                since=datetime.fromisoformat("2026-07-14T17:59:59+00:00"),
                options={},
                cursor=batch.next_cursor,
            )
        )
    )
    assert resumed[0].external_id not in batch.next_cursor["pending_ids"]
