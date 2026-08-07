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
from app.domain.importance import (
    calculate_importance,
    calculate_message_priority,
    normalize_importance_analysis,
)
from app.domain.ontology import normalize_entities
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
        content_blocks=[{"id": "b0001", "type": "paragraph", "text": "英雄属性发生变化。"}],
        published_at=datetime(2026, 8, 2, 8),
    )
    db.add(raw)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw.id,
        normalized_title="26.16 版本更新",
        normalized_text="英雄属性发生变化。",
        summary="26.16 调整了英雄属性。",
        entities=[{"name": "26.16", "type": "patch"}],
        primary_topic="patch",
        subtopic="patch_notes",
        source_kind="first_party",
        information_stage="active",
        product_scope="lol_pc",
        importance_score=0.8,
        analysis_model="fixture",
    )
    db.add(item)
    db.commit()
    return item


def _importance_analysis(
    *,
    scale: str = "standard",
    audience_region: str = "global",
    competition_region: str = "none",
    prominence: str = "normal",
    skin_tier: str = "none",
    is_bulk_update: bool = False,
) -> dict[str, object]:
    return {
        "scale": scale,
        "audience_region": audience_region,
        "competition_region": competition_region,
        "prominence": prominence,
        "skin_tier": skin_tier,
        "is_bulk_update": is_bulk_update,
        "evidence": ["测试锚点"],
    }


def test_adjusts_claim_is_accepted_by_timeline_persistence(db: Session) -> None:
    item = _item(db)

    claim = persist_generated_claims(
        db,
        item,
        fact_claims=[
            {
                "subject": {"name": "某英雄", "type": "champion"},
                "predicate": "adjusts",
                "object": {"attribute": "技能数值"},
                "temporal_role": "event",
                "supersedes_hint": None,
            }
        ],
        attribution={
            "claimed_by": "测试信源",
            "stance": "asserts",
            "certainty": "confirmed",
        },
    )[0]

    assert claim.predicate == "adjusts"
    assert claim.claim_type == "fact_claim"


def test_ontology_and_importance_are_controlled_and_deterministic() -> None:
    with pytest.raises(ValueError, match="unsupported entity type"):
        normalize_entities([{"name": "某对象", "type": "invented"}])
    patch_score, calculation = calculate_importance(
        _importance_analysis(),
        primary_topic="patch",
        subtopic="patch_notes",
    )
    community_score, _ = calculate_importance(
        _importance_analysis(),
        primary_topic="community",
        subtopic="community_post",
    )
    assert patch_score == 0.92
    assert community_score == 0.34
    assert calculation["final_score"] == patch_score


def test_editorial_policy_calibrates_transfer_and_shop_rotation() -> None:
    roster_score, roster_calculation = calculate_importance(
        _importance_analysis(prominence="star"),
        primary_topic="roster",
        subtopic="roster_move",
    )
    shop_score, shop_calculation = calculate_importance(
        _importance_analysis(),
        primary_topic="commerce",
        subtopic="shop_rotation",
        content="8月6日国服神话商城每日轮换。",
    )
    assert roster_score == 0.69
    assert roster_calculation["score_band"]["cap"] == 0.74
    assert shop_score == 0.48
    assert shop_calculation["editorial_subtype"] == "shop_daily_standard"


def test_editorial_importance_anchor_set() -> None:
    cases = [
        ("commerce", "shop_rotation", "每日轮换", {}, 0.48),
        ("commerce", "shop_rotation", "皮肤轮换", {}, 0.58),
        ("commerce", "shop_rotation", "限定皮肤轮换", {}, 0.66),
        ("commerce", "shop_rotation", "批量上新", {}, 0.66),
        ("patch", "patch_preview", "版本预览", {}, 0.86),
        ("patch", "patch_preview", "Full Preview", {}, 0.90),
        ("patch", "patch_notes", "版本公告", {}, 0.92),
        ("patch", "hotfix", "热修复", {"scale": "minor"}, 0.65),
        ("patch", "hotfix", "热修复", {"scale": "major"}, 0.81),
        ("champion", "champion_release", "新英雄", {}, 0.93),
        ("game_mode", "game_mode_release", "新模式", {}, 0.91),
        ("activity", "event_pass", "付费通行证", {}, 0.68),
        ("activity", "in_game_activity", "游戏内活动", {}, 0.72),
        ("activity", "in_game_activity", "免费领取皮肤", {}, 0.88),
        ("activity", "free_reward", "限时口令可领取臻彩和皮肤奖励", {}, 0.88),
        ("activity", "free_reward", "皮肤活动掌盟可以领取了", {}, 0.88),
        ("activity", "free_reward", "战斗之夜皮肤开箱开始", {}, 0.88),
        ("activity", "in_game_activity", "抽奖有机会获得皮肤", {}, 0.72),
        ("business", "corporate", "公司公告", {}, 0.66),
        ("universe", "lore", "背景故事", {}, 0.66),
        (
            "esports",
            "match_result",
            "LPL 常规赛",
            {"competition_region": "lpl"},
            0.63,
        ),
        (
            "esports",
            "match_result",
            "LCK 常规赛",
            {"competition_region": "lck"},
            0.60,
        ),
        (
            "esports",
            "match_result",
            "地区联赛常规赛",
            {"competition_region": "other"},
            0.57,
        ),
        ("esports", "match_result", "季后赛", {}, 0.67),
        ("esports", "match_result", "总决赛", {}, 0.73),
        ("esports", "match_result", "Worlds 小组赛", {}, 0.65),
        ("esports", "match_result", "Worlds 总决赛", {}, 0.77),
        ("roster", "roster_move", "转会", {}, 0.62),
        ("roster", "roster_move", "明星选手转会", {"prominence": "star"}, 0.69),
        ("skin", "skin_release", "新皮肤", {"skin_tier": "standard"}, 0.68),
        ("skin", "skin_release", "新皮肤配套炫彩", {"skin_tier": "standard"}, 0.68),
        ("skin", "skin_release", "国服新增付费臻彩", {"skin_tier": "standard"}, 0.68),
        ("skin", "skin_release", "传说皮肤", {"skin_tier": "legendary"}, 0.72),
        ("skin", "skin_release", "至臻皮肤", {"skin_tier": "prestige_or_mythic"}, 0.74),
        ("skin", "skin_release", "终极皮肤", {"skin_tier": "ultimate"}, 0.78),
    ]
    for primary_topic, subtopic, content, kwargs, expected in cases:
        score, _ = calculate_importance(
            _importance_analysis(**kwargs),
            primary_topic=primary_topic,
            subtopic=subtopic,
            content=content,
        )
        assert score == expected, (primary_topic, subtopic, content)


def test_content_guardrails_distinguish_full_preview_and_hotfix() -> None:
    full_preview, full_calculation = calculate_importance(
        _importance_analysis(
            scale="major",
            is_bulk_update=True,
        ),
        primary_topic="patch",
        subtopic="patch_preview",
        content="Patch 26.13 Full Preview! 包含完整英雄和系统改动。",
    )
    hotfix, hotfix_calculation = calculate_importance(
        _importance_analysis(scale="minor"),
        primary_topic="patch",
        subtopic="hotfix",
        content="单英雄不停机更新公告。",
    )
    leaked_tuning, leaked_calculation = calculate_importance(
        _importance_analysis(scale="major"),
        primary_topic="champion",
        subtopic="champion_update",
        content="Azir Q base damage changed from 60-140 to 75-135.",
        source_kind="attributed_report",
    )

    assert full_preview == 0.93
    assert full_calculation["editorial_subtype"] == "patch_full_preview"
    assert hotfix == 0.65
    assert hotfix_calculation["editorial_subtype"] == "patch_hotfix"
    assert leaked_tuning == 0.65
    assert leaked_calculation["editorial_subtype"] == "pbe_change"


def test_cn_paid_chroma_cannot_infer_prestige_tier() -> None:
    analysis = _importance_analysis(skin_tier="prestige_or_mythic")
    normalized = normalize_importance_analysis(
        analysis,
        primary_topic="skin",
        subtopic="skin_release",
        content="国服26.15版本新增多款付费臻彩原画。",
    )
    score, calculation = calculate_importance(
        analysis,
        primary_topic="skin",
        subtopic="skin_release",
        content="国服26.15版本新增多款付费臻彩原画。",
    )

    assert normalized["skin_tier"] == "standard"
    assert score == 0.68
    assert calculation["modifiers"] == []


def test_cn_chroma_keeps_explicit_prestige_skin_tier() -> None:
    analysis = normalize_importance_analysis(
        _importance_analysis(skin_tier="prestige_or_mythic"),
        primary_topic="skin",
        subtopic="skin_release",
        content="活动包含新增臻彩与至臻皮肤。",
    )

    assert analysis["skin_tier"] == "prestige_or_mythic"


def test_reminder_changes_priority_without_changing_intrinsic_importance() -> None:
    score, _ = calculate_importance(
        _importance_analysis(competition_region="lpl"),
        primary_topic="esports",
        subtopic="match_result",
    )
    priority, calculation = calculate_message_priority(
        score,
        information_stage="reminder",
        content_form="original",
        audience_region="cn",
    )

    assert score == 0.63
    assert priority == 0.51
    assert calculation["intrinsic_score"] == score


def test_free_skin_reward_reminder_keeps_high_intrinsic_value() -> None:
    score, calculation = calculate_importance(
        _importance_analysis(),
        primary_topic="activity",
        subtopic="free_reward",
        content="经典活动皮肤现已开放领取",
    )
    priority, priority_calculation = calculate_message_priority(
        score,
        information_stage="reminder",
        content_form="original",
        audience_region="cn",
    )

    assert score == 0.88
    assert calculation["editorial_subtype"] == "activity_free_skin"
    assert priority == 0.76
    assert priority_calculation["modifier_total"] == -0.12


def test_international_scope_changes_priority_not_intrinsic_importance() -> None:
    cases = [
        ("commerce", "shop_rotation", "每日轮换", 0.48),
        ("commerce", "shop_rotation", "皮肤轮换", 0.58),
        ("activity", "in_game_activity", "游戏内活动", 0.72),
        ("patch", "patch_notes", "版本公告", 0.92),
    ]
    for primary_topic, subtopic, content, intrinsic_score in cases:
        cn, _ = calculate_importance(
            _importance_analysis(audience_region="cn"),
            primary_topic=primary_topic,
            subtopic=subtopic,
            content=content,
        )
        international, _ = calculate_importance(
            _importance_analysis(audience_region="international_only"),
            primary_topic=primary_topic,
            subtopic=subtopic,
            content=content,
        )
        priority, calculation = calculate_message_priority(
            international,
            information_stage="announcement",
            content_form="original",
            audience_region="international_only",
        )

        assert cn == intrinsic_score
        assert international == intrinsic_score
        assert priority == round(intrinsic_score - 0.12, 4)
        assert calculation["modifier_total"] == -0.12


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
        event_kind="gameplay_update",
        aggregation_strategy="patch_cycle",
        product_scope="lol_pc",
    )
    second = Event(
        title="26.16 后续影响",
        summary="用于验证 Claim 可关联多个事件。",
        event_kind="gameplay_update",
        aggregation_strategy="timeline",
        product_scope="lol_pc",
    )
    db.add(second)
    db.flush()
    db.add(EventClaim(event_id=second.id, claim_id=claim.id, relation="context"))
    db.commit()
    assert {link.event_id for link in claim.event_links} == {first.id, second.id}
    assert (
        next(link for link in claim.event_links if link.event_id == first.id).relation == "supports"
    )


def test_atomic_transfer_claims_form_a_supersession_timeline(
    db: Session,
) -> None:
    rumor = _item(db)
    rumor.normalized_title = "传闻：WBG 正在考虑打野 Beichuan"
    rumor.summary = "WBG 正在考虑 Beichuan 作为打野候选。"
    rumor.primary_topic = "roster"
    rumor.source_kind = "attributed_report"
    rumor.information_stage = "rumor"
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
        event_kind="roster_change",
        aggregation_strategy="timeline",
        product_scope="lol_esports",
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
        entities=[
            {"name": "Beichuan", "type": "player"},
            {"name": "WBG", "type": "team"},
        ],
        source_kind="first_party",
        information_stage="announcement",
        subtopic="roster_move",
        product_scope="lol_esports",
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
        event_kind="gameplay_update",
        aggregation_strategy="patch_cycle",
        product_scope="lol_pc",
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
        event_kind="gameplay_update",
        aggregation_strategy="patch_cycle",
        product_scope="lol_pc",
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
        event_kind="gameplay_update",
        aggregation_strategy="patch_cycle",
        product_scope="lol_pc",
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
    timeline = _call_tool(db, "get_event_timeline", {"event_id": event.id})
    assert timeline["claims"][0]["id"] == claim.id
    assert timeline["claims"][0]["event_relation"] == "supports"
    assert timeline["messages"][0]["source_url"] is None

    item.publication_status = "withdrawn"
    db.commit()
    historical_claims = _call_tool(db, "get_event_timeline", {"event_id": event.id})["claims"]
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
