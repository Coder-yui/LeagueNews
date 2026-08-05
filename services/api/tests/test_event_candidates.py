from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.services.event_aggregation import create_event
from app.services.event_candidates import (
    aggregation_routes,
    event_aggregation_policy,
    find_event_candidates,
    stable_event_key,
)


def _add_item(
    db: Session,
    *,
    source_id: int,
    index: int,
    title: str,
    published_at: datetime,
    entities: list[dict[str, str]] | None = None,
    category: str = "版本更新",
    content_type: str | None = None,
    primary_topic: str | None = None,
    revision: int = 1,
    supersedes_raw_item_id: int | None = None,
) -> NormalizedItem:
    raw = RawItem(
        source_id=source_id,
        external_id=f"candidate-{index}",
        native_title=title,
        content_blocks=[{"id": "b0001", "type": "paragraph", "text": title}],
        published_at=published_at,
        revision=revision,
        supersedes_raw_item_id=supersedes_raw_item_id,
    )
    db.add(raw)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw.id,
        normalized_title=title,
        normalized_text=title,
        summary=title,
        category=category,
        entities=entities or [],
        importance_score=0.5,
        credibility="official",
        credibility_score=1.0,
        credibility_evidence=[],
        target_language="zh-CN",
        translated_title=title,
        translated_content_blocks=[],
        translation_status="not_required",
        analysis_model="test",
        analysis_version="test",
        content_type=content_type,
        primary_topic=primary_topic or "other",
    )
    db.add(item)
    db.commit()
    return item


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


def test_stable_patch_key_requires_version_context(db: Session) -> None:
    source = Source(name="Key Source", connector_type="manual")
    db.add(source)
    db.commit()
    patch = _add_item(
        db,
        source_id=source.id,
        index=1,
        title="Patch 26.13 Full Preview",
        published_at=datetime(2026, 6, 17, tzinfo=UTC),
    )
    unrelated = _add_item(
        db,
        source_id=source.id,
        index=2,
        title="比分 26.13",
        published_at=datetime(2026, 6, 17, tzinfo=UTC),
        category="赛事",
    )

    assert stable_event_key(patch) == "patch:26.13"
    assert stable_event_key(unrelated) is None


def test_stable_keys_cover_lpl_matches_and_transfer_claims(db: Session) -> None:
    source = Source(name="Event Identity", connector_type="manual")
    db.add(source)
    db.commit()
    match = _add_item(
        db,
        source_id=source.id,
        index=10,
        title="2026 LPL 夏季赛 BLG 2:1 击败 TES",
        published_at=datetime(2026, 7, 20, tzinfo=UTC),
        entities=[
            {"name": "BLG", "type": "team"},
            {"name": "TES", "type": "team"},
        ],
        category="赛事",
    )
    transfer = _add_item(
        db,
        source_id=source.id,
        index=11,
        title="传闻：Knight 正与 BLG 接触",
        published_at=datetime(2026, 7, 21, tzinfo=UTC),
        entities=[
            {"name": "Knight", "type": "player"},
            {"name": "BLG", "type": "team"},
        ],
        category="转会",
    )
    mode = _add_item(
        db,
        source_id=source.id,
        index=12,
        title="英雄联盟经典模式即将上线",
        published_at=datetime(2026, 7, 24, tzinfo=UTC),
        entities=[
            {
                "name": "经典模式",
                "canonical_name": "Classic Mode",
                "type": "game_mode",
            }
        ],
        category="游戏模式",
    )

    assert stable_event_key(match) == "matchday:lpl:2026-07-20"
    assert stable_event_key(transfer) == "transfer:2026:knight:blg"
    assert stable_event_key(mode) == "mode:classic-mode"


def test_program_routes_patch_component_roster_and_match_windows(
    db: Session,
) -> None:
    source = Source(name="Route Source", connector_type="riot_official")
    db.add(source)
    db.commit()
    patch = _add_item(
        db,
        source_id=source.id,
        index=13,
        title="26.15 更新公告：经典模式正式上线",
        published_at=datetime(2026, 7, 31, tzinfo=UTC),
        entities=[
            {"name": "26.15", "type": "patch", "role": "core"},
            {"name": "经典模式", "type": "game_mode", "role": "affected"},
        ],
        content_type="official_fact",
        primary_topic="patch",
    )
    roster = _add_item(
        db,
        source_id=source.id,
        index=14,
        title="传闻：WBG 正在考虑新的打野候选",
        published_at=datetime(2026, 7, 31, tzinfo=UTC),
        entities=[{"name": "WBG", "type": "team", "role": "core"}],
        category="转会",
        content_type="insider_rumor",
        primary_topic="roster",
    )
    regular = _add_item(
        db,
        source_id=source.id,
        index=15,
        title="LPL 常规赛赛果",
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
        entities=[{"name": "LPL", "type": "league", "role": "core"}],
        category="赛事",
        content_type="match_result",
        primary_topic="esports",
    )
    final = _add_item(
        db,
        source_id=source.id,
        index=16,
        title="LPL 总决赛赛果",
        published_at=datetime(2026, 8, 2, tzinfo=UTC),
        entities=[{"name": "LPL", "type": "tournament", "role": "core"}],
        category="赛事",
        content_type="match_result",
        primary_topic="esports",
    )

    patch_routes = aggregation_routes(patch)
    assert [
        (route.event_type, route.aggregation_key, route.membership_role)
        for route in patch_routes
    ] == [
        ("patch_cycle", "patch:26.15", "primary"),
        ("major_gameplay_change", "gameplay:经典模式", "component"),
    ]
    assert [
        (route.event_type, route.aggregation_key)
        for route in aggregation_routes(roster)
    ] == [("transfer_saga", "WBG:jungle:2026off")]
    assert [
        (route.event_type, route.aggregation_key)
        for route in aggregation_routes(regular)
    ] == [("daily_matches", "lpl:2026-08-01")]
    assert aggregation_routes(final)[0].event_type == "major_match"


def test_lpl_schedule_and_result_share_matchday_key_out_of_order(
    db: Session,
) -> None:
    source = Source(name="LPL Matchday", connector_type="weibo")
    db.add(source)
    db.commit()
    teams = [
        {"name": "LNG", "type": "team"},
        {"name": "NIP", "type": "team"},
        {"name": "BLG", "type": "team"},
    ]
    result = _add_item(
        db,
        source_id=source.id,
        index=20,
        title="2026LPL第三赛段7月26日赛果",
        published_at=datetime(2026, 7, 26, 14, 31, tzinfo=UTC),
        entities=teams,
        category="LPL赛程赛果",
    )
    schedule = _add_item(
        db,
        source_id=source.id,
        index=21,
        title="2026LPL第三赛段7月26日赛程预告",
        published_at=datetime(2026, 7, 26, 6, 11, tzinfo=UTC),
        entities=teams,
        category="LPL赛程",
    )

    key = "matchday:lpl:2026-07-26"
    assert stable_event_key(result) == key
    assert stable_event_key(schedule) == key
    event = create_event(
        db,
        normalized_item_id=result.id,
        event_key=key,
        title="2026LPL第三赛段7月26日赛果",
        summary="当日三场比赛已经结束。",
        category="LPL赛程赛果",
        event_type="match",
        lifecycle_status="completed",
    )

    candidates = find_event_candidates(db, normalized_item_id=schedule.id)
    assert candidates[0].event_id == event.id
    assert candidates[0].event_key == key
    assert candidates[0].score >= 100
    assert "发布时间相距 0 天" in candidates[0].reasons


def test_lpl_late_playoff_series_gets_individual_match_key(
    db: Session,
) -> None:
    source = Source(name="LPL Playoffs", connector_type="weibo")
    db.add(source)
    db.commit()
    semifinal = _add_item(
        db,
        source_id=source.id,
        index=22,
        title="2026LPL季后赛半决赛7月30日 BLG 对阵 TES",
        published_at=datetime(2026, 7, 30, 10, tzinfo=UTC),
        entities=[
            {"name": "BLG", "type": "team"},
            {"name": "TES", "type": "team"},
        ],
        category="LPL赛事",
    )

    assert stable_event_key(semifinal) == "match:lpl:2026-07-30:blg-vs-tes"


def test_candidate_search_returns_exact_patch_match_with_reasons(db: Session) -> None:
    source = Source(name="Candidate Source", connector_type="manual")
    db.add(source)
    db.commit()
    preview = _add_item(
        db,
        source_id=source.id,
        index=1,
        title="26.13 版本预览",
        published_at=datetime(2026, 6, 16, tzinfo=UTC),
        entities=[{"name": "26.13", "type": "patch"}],
    )
    event = create_event(
        db,
        normalized_item_id=preview.id,
        event_key="patch:26.13",
        title="英雄联盟 26.13 版本预览",
        summary="初始预览",
        category="版本更新",
    )
    full_preview = _add_item(
        db,
        source_id=source.id,
        index=2,
        title="26.13 版本完整预览",
        published_at=datetime(2026, 6, 17, tzinfo=UTC),
        entities=[{"name": "26.13", "type": "patch"}],
    )

    first = find_event_candidates(db, normalized_item_id=full_preview.id)
    repeated = find_event_candidates(db, normalized_item_id=full_preview.id)

    assert first == repeated
    assert first[0].event_id == event.id
    assert first[0].score >= 100
    assert any("聚合键精确匹配" in reason for reason in first[0].reasons)


def test_candidate_search_can_return_zero_candidates(db: Session) -> None:
    source = Source(name="Zero Source", connector_type="manual")
    db.add(source)
    db.commit()
    existing = _add_item(
        db,
        source_id=source.id,
        index=1,
        title="职业联赛赛果",
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        entities=[{"name": "Team A", "type": "team"}],
        category="赛事",
    )
    create_event(
        db,
        normalized_item_id=existing.id,
        title="一月职业联赛",
        summary="赛果",
        category="赛事",
    )
    incoming = _add_item(
        db,
        source_id=source.id,
        index=2,
        title="全新皮肤上线",
        published_at=datetime(2026, 7, 1, tzinfo=UTC),
        entities=[{"name": "Champion B", "type": "champion"}],
        category="皮肤",
    )

    assert find_event_candidates(db, normalized_item_id=incoming.id) == []


def test_candidate_search_is_ranked_and_capped_at_eight(db: Session) -> None:
    source = Source(name="Limit Source", connector_type="manual")
    db.add(source)
    db.commit()
    base_time = datetime(2026, 7, 1, tzinfo=UTC)
    for index in range(9):
        member = _add_item(
            db,
            source_id=source.id,
            index=index,
            title=f"阿狸平衡调整 {index}",
            published_at=base_time - timedelta(days=index),
            entities=[{"name": "阿狸", "type": "champion"}],
            category="英雄平衡",
        )
        create_event(
            db,
            normalized_item_id=member.id,
            title=f"阿狸平衡调整事件 {index}",
            summary="测试",
            category="英雄平衡",
        )
    incoming = _add_item(
        db,
        source_id=source.id,
        index=100,
        title="阿狸平衡调整后续",
        published_at=base_time,
        entities=[{"name": "阿狸", "type": "champion"}],
        category="英雄平衡",
    )

    candidates = find_event_candidates(db, normalized_item_id=incoming.id)

    assert len(candidates) == 8
    assert [candidate.score for candidate in candidates] == sorted(
        (candidate.score for candidate in candidates),
        reverse=True,
    )
    with pytest.raises(ValueError, match="between 1 and 8"):
        find_event_candidates(db, normalized_item_id=incoming.id, limit=9)


def test_superseded_event_member_is_an_exact_candidate(db: Session) -> None:
    source = Source(name="Revision Candidate", connector_type="riot_official")
    db.add(source)
    db.commit()
    old = _add_item(
        db,
        source_id=source.id,
        index=200,
        title="艺术家长廊开放申请",
        published_at=datetime(2026, 7, 22, tzinfo=UTC),
        category="社区活动",
    )
    event = create_event(
        db,
        normalized_item_id=old.id,
        title="艺术家长廊开放申请",
        summary="开放申请。",
        category="社区活动",
    )
    new = _add_item(
        db,
        source_id=source.id,
        index=201,
        title="完全不同的页面标题",
        published_at=datetime(2026, 9, 22, tzinfo=UTC),
        category="其他",
        revision=2,
        supersedes_raw_item_id=old.raw_item_id,
    )

    candidates = find_event_candidates(db, normalized_item_id=new.id)

    assert candidates[0].event_id == event.id
    assert candidates[0].score >= 200
    assert "当前消息是该事件成员的更新版本" in candidates[0].reasons


def test_game_mode_candidate_survives_category_drift_and_reverse_time_order(
    db: Session,
) -> None:
    source = Source(name="Mode Candidate", connector_type="riot_official")
    db.add(source)
    db.commit()
    later_announcement = _add_item(
        db,
        source_id=source.id,
        index=300,
        title="英雄联盟经典模式7月30日上线",
        published_at=datetime(2026, 7, 24, tzinfo=UTC),
        entities=[
            {
                "name": "经典模式",
                "canonical_name": "Classic Mode",
                "type": "game_mode",
            }
        ],
        category="游戏模式",
    )
    event = create_event(
        db,
        normalized_item_id=later_announcement.id,
        title="英雄联盟经典模式将于7月30日上线",
        summary="官方公布经典模式上线日期。",
        category="游戏模式",
        event_type="major_gameplay_change",
    )
    earlier_reveal = _add_item(
        db,
        source_id=source.id,
        index=301,
        title="英雄联盟经典模式正式公布",
        published_at=datetime(2026, 7, 14, tzinfo=UTC),
        entities=[
            {
                "name": "经典模式",
                "canonical_name": "Classic Mode",
                "type": "game_mode",
            }
        ],
        category="新模式",
    )

    candidates = find_event_candidates(
        db,
        normalized_item_id=earlier_reveal.id,
    )

    assert candidates[0].event_id == event.id
    assert any("分类归一一致" in reason for reason in candidates[0].reasons)
    assert any("相距 10 天" in reason for reason in candidates[0].reasons)


def test_dependent_asset_gets_broad_parent_event_candidate(db: Session) -> None:
    official = Source(name="Mode Official", connector_type="riot_official")
    community = Source(name="PBE Observer", connector_type="baidu_tieba")
    db.add_all([official, community])
    db.commit()
    reveal = _add_item(
        db,
        source_id=official.id,
        index=400,
        title="英雄联盟经典模式正式公布",
        published_at=datetime(2026, 7, 14, tzinfo=UTC),
        entities=[
            {
                "name": "经典模式",
                "canonical_name": "Classic Mode",
                "type": "game_mode",
            }
        ],
        category="新模式",
    )
    event = create_event(
        db,
        normalized_item_id=reveal.id,
        event_key="mode:classic-mode",
        title="拳头宣布推出英雄联盟经典模式",
        summary="经典模式将重现早期英雄联盟玩法。",
        category="新模式",
        event_type="major_gameplay_change",
    )
    dependent_asset = _add_item(
        db,
        source_id=community.id,
        index=401,
        title="测试服怀旧玩法礼包封面曝光",
        published_at=datetime(2026, 7, 22, tzinfo=UTC),
        entities=[
            {
                "name": "怀旧玩法礼包",
                "canonical_name": "Retro Bundle",
                "type": "item",
            }
        ],
        category="测试服资讯",
    )

    candidates = find_event_candidates(
        db,
        normalized_item_id=dependent_asset.id,
    )

    assert candidates[0].event_id == event.id
    assert candidates[0].match_level == "broad"
    assert candidates[0].summary == "经典模式将重现早期英雄联盟玩法。"
    assert candidates[0].core_entities == ("classic mode",)
    assert any("宽召回候选" in reason for reason in candidates[0].reasons)


def test_cn_mythic_shop_rotations_share_weekly_event_identity(db: Session) -> None:
    source = Source(name="国服商城观察", connector_type="baidu_tieba")
    db.add(source)
    db.commit()
    weekly = _add_item(
        db,
        source_id=source.id,
        index=500,
        title="神话商城每周更新：炫彩轮换",
        published_at=datetime(2026, 7, 23, tzinfo=UTC),
        entities=[
            {
                "name": "神话商城",
                "canonical_name": "Mythic Shop",
                "type": "game_feature",
            }
        ],
        category="国服活动",
        content_type="aggregation",
        primary_topic="activity",
    )
    daily = _add_item(
        db,
        source_id=source.id,
        index=501,
        title="7月24日神话商城每日轮换",
        published_at=datetime(2026, 7, 24, tzinfo=UTC),
        entities=[
            {
                "name": "神话商城",
                "canonical_name": "Mythic Shop",
                "type": "game_system",
            }
        ],
        category="游戏活动",
        content_type="aggregation",
        primary_topic="activity",
    )

    assert stable_event_key(weekly) == "mythic_shop:week:30"
    assert stable_event_key(daily) == "mythic_shop:week:30"
    assert event_aggregation_policy(daily)["cadence"] == "daily"

    event = create_event(
        db,
        normalized_item_id=weekly.id,
        event_key=stable_event_key(weekly),
        aggregation_key=stable_event_key(weekly),
        title="2026年第30周国服神话商城轮换",
        summary="本周神话商城进行轮换。",
        category="国服活动",
        event_type="shop_rotation",
        importance_score=0.4,
    )
    candidates = find_event_candidates(db, normalized_item_id=daily.id)

    assert candidates[0].event_id == event.id
    assert candidates[0].match_level == "strong"
    assert any("聚合键精确匹配" in reason for reason in candidates[0].reasons)


def test_x_mythic_shop_rotation_is_not_a_cn_event(db: Session) -> None:
    source = Source(
        name="SkinSpotlights",
        connector_type="x_twitter",
        external_key="skinspotlights",
    )
    db.add(source)
    db.commit()
    item = _add_item(
        db,
        source_id=source.id,
        index=510,
        title="Mythic Shop weekly rotation",
        published_at=datetime(2026, 7, 23, tzinfo=UTC),
        entities=[
            {
                "name": "神话商城",
                "canonical_name": "Mythic Shop",
                "type": "game_feature",
            }
        ],
        category="皮肤资讯",
    )

    policy = event_aggregation_policy(item)

    assert policy["region"] == "international"
    assert policy["event_eligible"] is False
    assert stable_event_key(item) is None
