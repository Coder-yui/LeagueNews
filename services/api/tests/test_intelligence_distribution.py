from datetime import datetime
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.api.routes.feeds import digest_feed, event_feed
from app.api.routes.mcp import _call_tool, _tool_definitions
from app.core.database import Base
from app.core.database import get_db
from app.domain.importance import DIMENSIONS, calculate_importance
from app.domain.ontology import normalize_entities, topic_from_category
from app.models.event import Event, EventRevision
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.services.claims import extract_traceable_claim
from app.services.digests import generate_digest
from app.services.event_aggregation import create_event
from app.main import app


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


def _item(db: Session) -> NormalizedItem:
    source = Source(name="Distribution source", connector_type="manual")
    db.add(source)
    db.flush()
    raw = RawItem(
        source_id=source.id,
        external_id="distribution-1",
        native_title="26.16 版本更新",
        content_blocks=[
            {"id": "b0001", "type": "paragraph", "text": "英雄属性发生变化。"}
        ],
        published_at=datetime(2026, 8, 2, 8),
    )
    db.add(raw)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw.id,
        normalized_title="26.16 版本更新",
        normalized_text="英雄属性发生变化。",
        summary="26.16 调整了英雄属性。",
        category="版本更新",
        entities=[{"name": "26.16", "type": "patch"}],
        primary_topic="patch",
        importance_score=0.8,
        credibility="official",
        credibility_score=1,
        credibility_evidence=[],
        analysis_model="fixture",
    )
    db.add(item)
    db.commit()
    return item


def test_ontology_and_importance_are_controlled_and_deterministic() -> None:
    assert topic_from_category("LPL 赛事赛果") == "esports"
    assert normalize_entities([{"name": "某对象", "type": "invented"}])[0]["type"] == "other"
    dimensions = {
        name: {"score": 3, "evidence": f"{name} evidence"} for name in DIMENSIONS
    }
    patch_score, calculation = calculate_importance(
        dimensions, primary_topic="patch"
    )
    community_score, _ = calculate_importance(
        dimensions, primary_topic="community"
    )
    assert patch_score == 0.75
    assert community_score == 0.5
    assert calculation["final_score"] == patch_score


def test_claim_traces_to_raw_block_and_can_feed_multiple_events(db: Session) -> None:
    item = _item(db)
    claim = extract_traceable_claim(db, item)
    db.commit()
    assert claim.evidence[0]["block_id"] == "b0001"
    first = create_event(
        db,
        normalized_item_id=item.id,
        title="26.16 更新",
        summary="版本发生调整。",
        category="版本更新",
    )
    # EventClaim is many-to-many even while the compatibility EventMessage layer
    # intentionally keeps one active primary event per message.
    from app.models.intelligence import EventClaim

    second = Event(
        title="26.16 后续影响",
        summary="用于验证 Claim 可关联多个事件。",
        category="版本更新",
    )
    db.add(second)
    db.flush()
    db.add(EventClaim(event_id=second.id, claim_id=claim.id, relation="context"))
    db.commit()
    assert {link.event_id for link in claim.event_links} == {first.id, second.id}


def test_digest_is_idempotent_revisable_and_feeds_are_valid_xml(db: Session) -> None:
    item = _item(db)
    event = create_event(
        db,
        normalized_item_id=item.id,
        title="26.16 更新",
        summary="版本发生调整。",
        category="版本更新",
    )
    cutoff = datetime(2026, 8, 3, 12)
    digest = generate_digest(db, digest_type="daily", cutoff_at=cutoff)
    again = generate_digest(db, digest_type="daily", cutoff_at=cutoff)
    assert again.id == digest.id
    assert again.current_revision == 1

    db.add(
        EventRevision(
            event_id=event.id,
            revision=2,
            title="26.16 热修",
            summary="新增一项修正。",
            change_note="late correction",
            evidence_snapshot={},
            created_at=datetime(2026, 8, 3, 10),
        )
    )
    db.commit()
    revised = generate_digest(db, digest_type="daily", cutoff_at=cutoff)
    assert revised.current_revision == 2
    assert "热修" in revised.body

    event_xml = event_feed(limit=50, db=db)
    digest_xml = digest_feed(limit=30, db=db)
    for response in (event_xml, digest_xml):
        ElementTree.fromstring(response.body)
    assert f"event:{event.id}</guid>" in event_xml.body.decode()
    assert f"digest:{digest.id}</guid>" in digest_xml.body.decode()


def test_mcp_surface_is_read_only_and_returns_structured_provenance(db: Session) -> None:
    item = _item(db)
    claim = extract_traceable_claim(db, item)
    db.commit()
    event = create_event(
        db,
        normalized_item_id=item.id,
        title="26.16 更新",
        summary="版本发生调整。",
        category="版本更新",
    )
    tools = _tool_definitions()
    assert {tool["name"] for tool in tools} == {
        "list_events",
        "get_event",
        "get_event_timeline",
        "search_events",
        "list_digests",
        "get_digest",
    }
    assert all(tool["annotations"]["readOnlyHint"] is True for tool in tools)
    timeline = _call_tool(
        db, "get_event_timeline", {"event_id": event.id}
    )
    assert timeline["claims"][0]["id"] == claim.id
    assert timeline["messages"][0]["source_url"] is None

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        initialized = client.post(
            "/api/v1/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert initialized.json()["result"]["protocolVersion"] == "2025-11-25"
        rejected_origin = client.post(
            "/api/v1/mcp",
            headers={"origin": "https://attacker.example"},
            json={"jsonrpc": "2.0", "id": 9, "method": "tools/list"},
        )
        assert rejected_origin.status_code == 403
        listed = client.post(
            "/api/v1/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert len(listed.json()["result"]["tools"]) == 6
        called = client.post(
            "/api/v1/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "get_event_timeline",
                    "arguments": {"event_id": event.id},
                },
            },
        )
        assert called.json()["result"]["structuredContent"]["claims"][0]["id"] == claim.id
    finally:
        app.dependency_overrides.clear()
