from datetime import UTC, datetime
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.routes.feeds import digest_feed, event_feed
from app.api.routes.mcp import _call_tool, _tool_definitions
from app.core.database import Base
from app.core.database import get_db
from app.domain.importance import calculate_importance
from app.domain.ontology import normalize_entities, topic_from_category
from app.models.event import Event, EventRevision
from app.models.intelligence import Claim, EventClaim
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.services.claims import (
    backfill_published_claims,
    extract_traceable_claim,
    persist_generated_claims,
)
from app.services.digests import generate_digest
from app.services.event_aggregation import add_message_to_event, create_event
from app.main import app


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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
        analysis_model="fixture",
    )
    db.add(item)
    db.commit()
    return item


def _importance_analysis(
    editorial_subtype: str,
    *,
    scale: str = "standard",
    audience_region: str = "global",
    competition_region: str = "none",
    prominence: str = "normal",
    skin_tier: str = "none",
    is_bulk_update: bool = False,
    is_first_concrete_disclosure: bool = False,
    is_duplicate_or_reminder: bool = False,
) -> dict[str, object]:
    return {
        "editorial_subtype": editorial_subtype,
        "scale": scale,
        "audience_region": audience_region,
        "competition_region": competition_region,
        "prominence": prominence,
        "skin_tier": skin_tier,
        "is_bulk_update": is_bulk_update,
        "is_first_concrete_disclosure": is_first_concrete_disclosure,
        "is_duplicate_or_reminder": is_duplicate_or_reminder,
        "evidence": ["测试锚点"],
    }


def test_ontology_and_importance_are_controlled_and_deterministic() -> None:
    assert topic_from_category("LPL 赛事赛果") == "esports"
    assert normalize_entities([{"name": "某对象", "type": "invented"}])[0]["type"] == "other"
    patch_score, calculation = calculate_importance(
        _importance_analysis("patch_official_notes"),
        primary_topic="patch",
    )
    community_score, _ = calculate_importance(
        _importance_analysis("community"),
        primary_topic="community",
    )
    assert patch_score == 0.92
    assert community_score == 0.4
    assert calculation["final_score"] == patch_score


def test_editorial_policy_calibrates_transfer_and_shop_rotation() -> None:
    roster_score, roster_calculation = calculate_importance(
        _importance_analysis("roster_transfer", prominence="star"),
        primary_topic="roster",
    )
    shop_score, shop_calculation = calculate_importance(
        _importance_analysis("shop_daily_standard"),
        primary_topic="activity",
        content="8月6日国服神话商城每日轮换。",
    )
    assert roster_score == 0.67
    assert roster_calculation["score_band"]["cap"] == 0.72
    assert shop_score == 0.5
    assert shop_calculation["editorial_subtype"] == "shop_daily_standard"


@pytest.mark.parametrize(
    ("subtype", "kwargs", "expected"),
    [
        ("shop_daily_standard", {}, 0.50),
        ("shop_cosmetic_rotation", {}, 0.60),
        ("shop_rare_cosmetic", {}, 0.66),
        ("shop_bulk_refresh", {}, 0.66),
        ("patch_preview", {}, 0.87),
        ("patch_full_preview", {}, 0.90),
        ("patch_official_notes", {}, 0.92),
        ("patch_hotfix", {"scale": "minor"}, 0.70),
        ("patch_hotfix", {"scale": "major"}, 0.85),
        ("new_champion", {}, 0.93),
        ("new_game_mode", {}, 0.92),
        ("activity_paid", {}, 0.75),
        ("activity_standard", {}, 0.78),
        ("activity_free_skin", {}, 0.90),
        ("riot_corporate", {}, 0.65),
        ("lol_universe", {}, 0.65),
        (
            "esports_regular",
            {"competition_region": "lpl"},
            0.63,
        ),
        (
            "esports_regular",
            {"competition_region": "lck"},
            0.60,
        ),
        (
            "esports_regular",
            {"competition_region": "other"},
            0.57,
        ),
        ("esports_playoffs", {}, 0.70),
        ("esports_final", {}, 0.73),
        ("worlds_regular", {}, 0.66),
        ("worlds_key", {}, 0.75),
        ("roster_transfer", {}, 0.60),
        ("roster_transfer", {"prominence": "star"}, 0.67),
        ("skin_release", {"skin_tier": "standard"}, 0.70),
        ("skin_release", {"skin_tier": "legendary"}, 0.74),
        ("skin_release", {"skin_tier": "prestige_or_mythic"}, 0.76),
        ("skin_release", {"skin_tier": "ultimate"}, 0.80),
    ],
)
def test_editorial_importance_anchor_set(
    subtype: str,
    kwargs: dict[str, object],
    expected: float,
) -> None:
    score, _ = calculate_importance(
        _importance_analysis(subtype, **kwargs),
        primary_topic="other",
    )
    assert score == expected


def test_content_guardrails_distinguish_full_preview_and_hotfix() -> None:
    full_preview, full_calculation = calculate_importance(
        _importance_analysis(
            "patch_preview",
            scale="major",
            is_bulk_update=True,
            is_first_concrete_disclosure=True,
        ),
        primary_topic="patch",
        content="Patch 26.13 Full Preview! 包含完整英雄和系统改动。",
    )
    hotfix, hotfix_calculation = calculate_importance(
        _importance_analysis("patch_official_notes", scale="minor"),
        primary_topic="patch",
        content="单英雄不停机更新公告。",
    )

    assert full_preview == 0.93
    assert full_calculation["editorial_subtype"] == "patch_full_preview"
    assert hotfix == 0.70
    assert hotfix_calculation["editorial_subtype"] == "patch_hotfix"


def test_duplicate_penalty_is_explicit_and_does_not_change_base_policy() -> None:
    score, calculation = calculate_importance(
        _importance_analysis(
            "esports_regular",
            competition_region="lpl",
            is_duplicate_or_reminder=True,
        ),
        primary_topic="esports",
    )

    assert score == 0.51
    assert calculation["band_score"] == 0.63
    assert calculation["duplicate_penalty"] == 0.12


@pytest.mark.parametrize(
    ("subtype", "cn_score", "international_score", "penalty"),
    [
        ("shop_daily_standard", 0.50, 0.30, 0.20),
        ("shop_cosmetic_rotation", 0.60, 0.40, 0.20),
        ("activity_standard", 0.78, 0.63, 0.15),
        ("patch_official_notes", 0.92, 0.77, 0.15),
    ],
)
def test_international_only_content_is_discounted_when_cn_is_unaffected(
    subtype: str,
    cn_score: float,
    international_score: float,
    penalty: float,
) -> None:
    cn, _ = calculate_importance(
        _importance_analysis(subtype, audience_region="cn"),
        primary_topic="other",
    )
    international, calculation = calculate_importance(
        _importance_analysis(subtype, audience_region="international_only"),
        primary_topic="other",
    )

    assert cn == cn_score
    assert international == international_score
    assert calculation["region_penalty"] == penalty


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
    assert next(
        link for link in claim.event_links if link.event_id == first.id
    ).relation == "supports"


def test_atomic_transfer_claims_form_a_supersession_timeline(
    db: Session,
) -> None:
    rumor = _item(db)
    rumor.normalized_title = "传闻：WBG 正在考虑打野 Beichuan"
    rumor.summary = "WBG 正在考虑 Beichuan 作为打野候选。"
    rumor.primary_topic = "roster"
    rumor.content_type = "insider_rumor"
    rumor_claim = persist_generated_claims(
        db,
        rumor,
        fact_claims=[
            {
                "subject": {"name": "Beichuan", "type": "player"},
                "predicate": "considered_for",
                "object": {"team": "WBG", "position": "jungle"},
                "temporal_role": "prediction",
                "supersedes_hint": None,
            }
        ],
        attribution={
            "claimed_by": "爆料人",
            "stance": "asserts",
            "certainty": "speculative",
        },
    )[0]
    event = create_event(
        db,
        normalized_item_id=rumor.id,
        aggregation_key="WBG:jungle:2026off",
        title="WBG 打野转会",
        summary="WBG 正在考察打野候选。",
        category="转会",
        event_type="transfer_saga",
        lifecycle_status="unconfirmed",
    )

    official_raw = RawItem(
        source_id=rumor.raw_item.source_id,
        external_id="distribution-official-transfer",
        native_title="WBG 官宣 Beichuan 加盟",
        content_blocks=[
            {
                "id": "b0001",
                "type": "paragraph",
                "text": "WBG 官宣 Beichuan 加盟并担任打野。",
            }
        ],
        published_at=datetime(2026, 8, 5, tzinfo=UTC),
    )
    db.add(official_raw)
    db.flush()
    official = NormalizedItem(
        raw_item_id=official_raw.id,
        normalized_title="WBG 官宣 Beichuan 加盟",
        normalized_text="WBG 官宣 Beichuan 加盟并担任打野。",
        summary="Beichuan 正式加盟 WBG。",
        category="转会",
        entities=[
            {"name": "Beichuan", "type": "player"},
            {"name": "WBG", "type": "team"},
        ],
        content_type="official_fact",
        primary_topic="roster",
        importance_score=0.6,
        analysis_model="fixture",
    )
    db.add(official)
    db.flush()
    official_claim = persist_generated_claims(
        db,
        official,
        fact_claims=[
            {
                "subject": {"name": "Beichuan", "type": "player"},
                "predicate": "transfers_to",
                "object": {"team": "WBG", "position": "jungle"},
                "temporal_role": "event",
                "supersedes_hint": "WBG",
            }
        ],
        attribution={
            "claimed_by": "WBG",
            "stance": "confirms",
            "certainty": "confirmed",
        },
    )[0]
    add_message_to_event(
        db,
        event_id=event.id,
        normalized_item_id=official.id,
        lifecycle_status="confirmed",
    )
    db.commit()

    db.refresh(official_claim)
    db.refresh(rumor_claim)
    assert official_claim.supersedes_claim_id == rumor_claim.id
    assert rumor_claim.status == "superseded"
    assert official_claim.attribution["claimed_by"] == "WBG"
    assert official_claim.temporal_role == "event"
    assert db.query(Claim).filter(Claim.claim_type == "fact_claim").count() == 2


def test_claim_backfill_creates_event_links_and_is_idempotent(
    db: Session,
) -> None:
    item = _item(db)
    event = create_event(
        db,
        normalized_item_id=item.id,
        title="历史事件",
        summary="历史消息已经属于事件。",
        category="版本更新",
    )

    dry_run = backfill_published_claims(db, apply=False)
    assert dry_run.claims_created == 1
    assert dry_run.event_claims_created == 1
    assert item.claims == []

    applied = backfill_published_claims(db, apply=True)
    db.commit()
    assert applied.claims_created == 1
    assert applied.event_claims_created == 1
    db.expire_all()
    refreshed_item = db.get(NormalizedItem, item.id)
    assert refreshed_item is not None
    assert refreshed_item.claims[0].event_links[0].event_id == event.id

    repeated = backfill_published_claims(db, apply=True)
    db.commit()
    assert repeated.claims_created == 0
    assert repeated.event_claims_created == 0
    db.expire_all()
    refreshed_item = db.get(NormalizedItem, item.id)
    assert refreshed_item is not None
    assert len(refreshed_item.claims) == 1
    assert len(refreshed_item.claims[0].event_links) == 1


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
            created_at=datetime(2026, 8, 3, 3, tzinfo=UTC),
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
    assert timeline["claims"][0]["event_relation"] == "supports"
    assert timeline["messages"][0]["source_url"] is None

    item.publication_status = "withdrawn"
    db.commit()
    historical_claims = _call_tool(
        db, "get_event_timeline", {"event_id": event.id}
    )["claims"]
    assert historical_claims[0]["id"] == claim.id
    assert historical_claims[0]["status"] == "active"
    item.publication_status = "published"
    db.commit()

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
