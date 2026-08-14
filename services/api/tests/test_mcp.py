import asyncio
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core import database as database_module
from app.core.database import Base
from app.mcp.http import MCPServiceTokenMiddleware
from app.mcp.server import mcp_server
from app.models.daily_report import DailyReport, DailyReportItem
from app.models.event import Event, EventMention
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from mcp import Client


@pytest.fixture
def mcp_database(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database_module, "SessionLocal", factory)

    with Session(engine, expire_on_commit=False) as db:
        source = Source(
            name="MCP public source",
            connector_type="test",
            base_url="https://example.com",
            reliability_score=0.9,
        )
        db.add(source)
        db.flush()
        raw = RawItem(
            source_id=source.id,
            external_id="mcp-faker",
            native_title="Public roster note",
            author_name="AuthorRecallTerm",
            canonical_url="https://example.com/faker",
            published_at=datetime(2026, 8, 14, 1, tzinfo=UTC),
            content_blocks=[
                {"type": "paragraph", "text": "BodyRecallTerm mentions Faker"},
                {"type": "image", "storage_path": "/private/not-public.jpg"},
            ],
        )
        withdrawn_raw = RawItem(
            source_id=source.id,
            external_id="mcp-withdrawn",
            native_title="Withdrawn",
            published_at=datetime(2026, 8, 14, 2, tzinfo=UTC),
            content_blocks=[{"type": "paragraph", "text": "Do not return"}],
        )
        db.add_all([raw, withdrawn_raw])
        db.flush()
        item = NormalizedItem(
            raw_item_id=raw.id,
            normalized_title="Public roster note",
            normalized_text="BodyRecallTerm mentions Faker",
            translated_title="公开阵容消息",
            translated_text="正文提到 Faker",
            translated_content_blocks=[{"type": "paragraph", "text": "公开消息"}],
            summary="公开消息摘要。",
            products=["lol_esports"],
            message_type="esports_announcement",
            topics=["esports_rosters"],
            content_form="original",
            importance_score=0.9,
            analysis_model="test",
            translation_status="translated",
        )
        withdrawn = NormalizedItem(
            raw_item_id=withdrawn_raw.id,
            normalized_title="Withdrawn",
            normalized_text="Withdrawn",
            summary="Withdrawn",
            products=["lol_esports"],
            message_type="esports_announcement",
            topics=["esports_rosters"],
            importance_score=0.95,
            publication_status="withdrawn",
            analysis_model="test",
            translation_status="not_required",
        )
        db.add_all([item, withdrawn])
        db.flush()
        event = Event(
            title="Faker roster update",
            current_summary="A current public event about Faker.",
            event_family="roster_change",
            products=["lol_esports"],
            lifecycle_status="unconfirmed",
            credibility_level="plausible",
            importance_score=0.8,
            heat_score=0.7,
            primary_source_message_id=item.id,
            last_seen_at=raw.published_at,
        )
        db.add(event)
        db.flush()
        db.add(
            EventMention(
                event_id=event.id,
                normalized_item_id=item.id,
                normalized_item_revision=1,
                mention_index=0,
                relation="reports",
                source_role="known_leaker",
                materiality="material_update",
                evidence_excerpt="Faker update evidence",
                source_published_at=raw.published_at,
            )
        )
        withdrawn_event = Event(
            title="Withdrawn event",
            current_summary="This event lost its public mention.",
            event_family="roster_change",
            products=["lol_esports"],
            lifecycle_status="stale",
            credibility_level="plausible",
            importance_score=0.7,
            heat_score=0.2,
        )
        db.add(withdrawn_event)
        db.flush()
        db.add(
            EventMention(
                event_id=withdrawn_event.id,
                normalized_item_id=withdrawn.id,
                normalized_item_revision=1,
                mention_index=0,
                relation="reports",
                source_role="known_leaker",
                materiality="material_update",
                evidence_excerpt="Withdrawn evidence",
                source_published_at=withdrawn_raw.published_at,
            )
        )
        report = DailyReport(report_date=date(2026, 8, 14), status="published")
        db.add(report)
        db.flush()
        db.add(
            DailyReportItem(
                report_id=report.id,
                normalized_item_id=item.id,
                section="esports",
                position=1,
            )
        )
        db.commit()


def _call(name: str, arguments: dict | None = None):
    async def run():
        async with Client(mcp_server) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(run())


def test_mcp_discovers_six_tools_and_reads_public_projections(mcp_database) -> None:
    async def list_names():
        async with Client(mcp_server) as client:
            result = await client.list_tools()
            return [tool.name for tool in result.tools]

    assert asyncio.run(list_names()) == [
        "search_news",
        "get_news_item",
        "search_events",
        "get_event",
        "get_daily_report",
        "get_latest_daily_report",
    ]

    news = _call("search_news", {"query": "Faker", "product": "lol_esports"})
    assert news.is_error is False
    assert news.structured_content["total"] == 1
    assert news.structured_content["items"][0]["related_event_ids"]

    item = _call("get_news_item", {"message_id": 1})
    assert item.structured_content["title"] == "公开阵容消息"
    assert "storage_path" not in item.structured_content["original_content_blocks"][1]
    assert item.structured_content["events"][0]["title"] == "Faker roster update"

    event = _call("search_events", {"credibility": "plausible"})
    assert event.structured_content["total"] == 1
    detail = _call("get_event", {"event_id": 1})
    assert detail.structured_content["evidence"][0]["message_id"] == 1

    hidden_event = _call("search_events", {"query": "Withdrawn event"})
    assert hidden_event.structured_content["total"] == 0
    hidden_detail = _call("get_event", {"event_id": 2})
    assert hidden_detail.is_error is True
    assert "not found" in str(hidden_detail.content[0].text).lower()

    report = _call("get_daily_report", {"report_date": "2026-08-14"})
    assert len(report.structured_content["sections"]["esports"]) == 1
    report_message = report.structured_content["sections"]["esports"][0]
    assert report_message["title"] == "公开阵容消息"
    assert report_message["summary"] == "公开消息摘要。"
    assert report_message["importance_score"] == 0.9
    assert "original_content_blocks" not in report_message
    assert "translated_content_blocks" not in report_message
    assert "media" not in report_message
    assert "related_event_ids" not in report_message
    latest = _call("get_latest_daily_report")
    assert latest.structured_content["report_date"] == "2026-08-14"
    assert "related_event_ids" not in latest.structured_content["sections"]["esports"][0]


def test_search_news_reaches_body_author_and_source(mcp_database) -> None:
    for query in ("BodyRecallTerm", "AuthorRecallTerm", "MCP public source"):
        result = _call("search_news", {"query": query})
        assert result.is_error is False
        assert result.structured_content["total"] == 1
        assert result.structured_content["items"][0]["id"] == 1


def test_mcp_unknown_ids_and_non_published_items_are_not_exposed(mcp_database) -> None:
    result = _call("search_news", {"query": "Withdrawn"})
    assert result.structured_content["total"] == 0

    unknown = _call("get_news_item", {"message_id": 999})
    assert unknown.is_error is True
    assert "not found" in str(unknown.content[0].text).lower()

    no_report = _call("get_daily_report", {"report_date": "2026-08-13"})
    assert no_report.is_error is True
    assert "no published daily report" in str(no_report.content[0].text).lower()


def test_mcp_service_token_header_protects_transport() -> None:
    calls: list[str] = []

    async def downstream(scope, receive, send):
        calls.append("called")
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = MCPServiceTokenMiddleware(
        downstream,
        header="X-MCP-Service-Token",
        token="test-secret",
    )

    async def invoke(value: str | None):
        messages = []
        headers = [] if value is None else [(b"x-mcp-service-token", value.encode())]

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await middleware(
            {"type": "http", "method": "POST", "headers": headers},
            receive,
            send,
        )
        return messages

    denied = asyncio.run(invoke(None))
    accepted = asyncio.run(invoke("test-secret"))
    assert denied[0]["status"] == 401
    assert accepted[0]["status"] == 204
    assert calls == ["called"]


def test_agent_scenario_filters_are_expressible(mcp_database) -> None:
    scenarios = [
        ("search_news", {"product": "lol_pc", "min_importance": 0.75, "limit": 5}),
        ("search_news", {"query": "Faker", "since": "2026-08-13T00:00:00Z"}),
        ("search_news", {"product": "tft", "sort_by": "importance"}),
        (
            "search_events",
            {"product": "lol_esports", "lifecycle": "unconfirmed", "heat": "hot"},
        ),
        ("search_events", {"event_family": "roster_change", "sort_by": "importance"}),
    ]
    for tool_name, arguments in scenarios:
        result = _call(tool_name, arguments)
        assert result.is_error is False, (tool_name, result.content)
