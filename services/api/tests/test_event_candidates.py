from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.domain.event_clusters import is_marquee_match
from app.domain.ontology import normalize_entities, normalize_event_mentions
from app.models.intelligence import Claim
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.services.event_aggregation import create_event
from app.services.event_candidates import (
    aggregation_routes,
    find_event_candidates,
    resolve_aggregation_routes,
)


def _stable_key(item: NormalizedItem) -> str | None:
    routes = aggregation_routes(item)
    return routes[0].aggregation_key if routes else None


def _add_item(
    db: Session,
    *,
    source_id: int,
    index: int,
    title: str,
    published_at: datetime,
    entities: list[dict[str, str]] | None = None,
    primary_topic: str = "patch",
    subtopic: str = "patch_preview",
    source_kind: str = "attributed_report",
    information_stage: str = "preview",
    content_form: str = "original",
    product_scope: str = "lol_pc",
    facets: dict[str, object] | None = None,
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
        entities=entities or [],
        primary_topic=primary_topic,
        subtopic=subtopic,
        source_kind=source_kind,
        information_stage=information_stage,
        content_form=content_form,
        product_scope=product_scope,
        facets=facets or {},
        importance_score=0.5,
        target_language="zh-CN",
        translated_title=title,
        translated_content_blocks=[],
        translation_status="not_required",
        analysis_model="test",
        analysis_version="test",
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
        primary_topic="esports",
        subtopic="match_result",
    )

    assert _stable_key(patch) == "patch:lol_pc:26.13"
    assert _stable_key(unrelated) is None


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
            {"name": "LPL", "type": "league"},
            {"name": "BLG", "type": "team"},
            {"name": "TES", "type": "team"},
        ],
        primary_topic="esports",
        subtopic="match_result",
        product_scope="lol_esports",
        facets={"temporal": {"event_date": "2026-07-20"}},
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
        primary_topic="roster",
        subtopic="roster_move",
        information_stage="rumor",
        product_scope="lol_esports",
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
                "role": "core",
            }
        ],
        primary_topic="game_mode",
        subtopic="game_mode_release",
    )

    assert _stable_key(match) == "matchday:lpl:2026-07-20"
    assert _stable_key(transfer) == "transfer:2026:knight"
    assert _stable_key(mode) == "release:lol_pc:classic-mode"


@pytest.mark.parametrize(
    ("subtopic", "title", "cosmetic_name"),
    [
        ("skin_release", "花仙子拉克丝全新炫彩公布", "花仙子拉克丝炫彩"),
        ("skin_release", "国服新增星神臻彩", "星神臻彩"),
    ],
)
def test_new_cosmetics_share_skin_release_event_routing(
    db: Session,
    subtopic: str,
    title: str,
    cosmetic_name: str,
) -> None:
    source = Source(name="Cosmetic release", connector_type="tencent_lol")
    db.add(source)
    db.commit()
    item = _add_item(
        db,
        source_id=source.id,
        index=130,
        title=title,
        published_at=datetime(2026, 8, 7, tzinfo=UTC),
        entities=[
            {
                "name": cosmetic_name,
                "type": "skin",
                "role": "core",
            }
        ],
        primary_topic="skin",
        subtopic=subtopic,
    )

    route = aggregation_routes(item)[0]
    assert route.event_kind == "cosmetic_release"
    assert route.aggregation_strategy == "release"
    assert route.aggregation_key == f"release:lol_pc:{cosmetic_name}"


def test_same_release_batch_becomes_one_cosmetic_event(db: Session) -> None:
    source = Source(name="Release batch", connector_type="riot_official")
    db.add(source)
    db.commit()
    mentions = normalize_event_mentions(
        [
            {
                "topic": "skin",
                "subtopic": "skin_release",
                "identity_entities": [{"name": name, "type": "skin", "role": "core"}],
                "assertion": "asserted",
                "temporal": {"event_date": "2026-08-12"},
                "membership_role": "primary" if index == 0 else "component",
            }
            for index, name in enumerate(("星界拉克丝", "星界阿狸", "星界金克丝"))
        ]
    )
    item = _add_item(
        db,
        source_id=source.id,
        index=131,
        title="26.16版本星界系列皮肤与配套炫彩",
        published_at=datetime(2026, 8, 5, tzinfo=UTC),
        primary_topic="skin",
        subtopic="skin_release",
        facets={"event_mentions": mentions},
    )

    routes = aggregation_routes(item)
    assert len(routes) == 1
    assert routes[0].event_kind == "cosmetic_release"
    assert routes[0].aggregation_key == "cosmetic_batch:lol_pc:26.16"


def test_activity_and_same_day_cosmetics_form_two_topic_clusters(
    db: Session,
) -> None:
    source = Source(name="Mixed release", connector_type="riot_official")
    db.add(source)
    db.commit()
    activity = {
        "topic": "activity",
        "subtopic": "event_pass",
        "identity_entities": [{"name": "经典模式通行证", "type": "activity", "role": "core"}],
        "assertion": "asserted",
        "temporal": {"event_date": "2026-07-30"},
        "membership_role": "primary",
    }
    cosmetics = [
        {
            "topic": "skin",
            "subtopic": "skin_release",
            "identity_entities": [{"name": name, "type": "skin", "role": "core"}],
            "assertion": "asserted",
            "temporal": {"event_date": "2026-07-30"},
            "membership_role": "component",
        }
        for name in ("经典贾克斯", "经典李青", "经典安妮")
    ]
    item = _add_item(
        db,
        source_id=source.id,
        index=132,
        title="经典模式通行证与配套皮肤上线",
        published_at=datetime(2026, 7, 30, tzinfo=UTC),
        primary_topic="activity",
        subtopic="event_pass",
        facets={"event_mentions": normalize_event_mentions([activity, *cosmetics])},
    )

    assert [route.aggregation_key for route in aggregation_routes(item)] == [
        "activity:lol_pc:经典模式通行证",
        "cosmetic_batch:lol_pc:2026-07-30",
    ]


def test_esports_route_resolves_relative_match_date(db: Session) -> None:
    source = Source(name="Undated schedule", connector_type="weibo")
    db.add(source)
    db.commit()
    schedule = _add_item(
        db,
        source_id=source.id,
        index=120,
        title="LPL 今日赛程预告",
        published_at=datetime(2026, 8, 6, tzinfo=UTC),
        entities=[
            {"name": "LPL", "type": "league", "role": "context"},
            {"name": "BLG", "type": "team", "role": "core"},
            {"name": "TES", "type": "team", "role": "core"},
        ],
        primary_topic="esports",
        subtopic="match_schedule",
        product_scope="lol_esports",
        facets={"temporal": {"event_date": None}},
    )

    assert _stable_key(schedule) == "matchday:lpl:2026-08-06"


@pytest.mark.parametrize("league", ["LPL", "LCK"])
def test_one_regular_match_still_uses_the_league_matchday(
    db: Session,
    league: str,
) -> None:
    source = Source(name=f"{league} schedule", connector_type="weibo")
    db.add(source)
    db.commit()
    item = _add_item(
        db,
        source_id=source.id,
        index=133,
        title=f"{league} 常规赛 Alpha 对阵 Beta",
        published_at=datetime(2026, 8, 8, tzinfo=UTC),
        entities=[
            {"name": league, "type": "league", "role": "context"},
            {"name": "Alpha", "type": "team", "role": "core"},
            {"name": "Beta", "type": "team", "role": "core"},
        ],
        primary_topic="esports",
        subtopic="match_schedule",
        product_scope="lol_esports",
        facets={"temporal": {"event_date": "2026-08-09"}},
    )

    assert _stable_key(item) == f"matchday:{league.casefold()}:2026-08-09"


def test_lpl_stage_names_identify_a_regular_matchday_without_league_entity(
    db: Session,
) -> None:
    source = Source(name="LPL commentator", connector_type="weibo")
    db.add(source)
    db.commit()
    item = _add_item(
        db,
        source_id=source.id,
        index=134,
        title="聊完登峰组，涅槃组明天第一场 WBG 对阵 NIP",
        published_at=datetime(2026, 7, 29, 15, tzinfo=UTC),
        entities=[
            {"name": "WBG", "type": "team", "role": "core"},
            {"name": "NIP", "type": "team", "role": "core"},
        ],
        primary_topic="esports",
        subtopic="match_schedule",
        product_scope="lol_esports",
        facets={"temporal": {"event_date": "2026-07-30"}},
    )

    assert _stable_key(item) == "matchday:lpl:2026-07-30"


def test_unscoped_match_result_resolves_to_the_existing_same_team_matchday(
    db: Session,
) -> None:
    official = Source(name="LPL official", connector_type="weibo")
    commentator = Source(name="Match commentator", connector_type="weibo")
    db.add_all([official, commentator])
    db.commit()
    schedule = _add_item(
        db,
        source_id=official.id,
        index=140,
        title="LPL 7月30日赛程预告",
        published_at=datetime(2026, 7, 29, tzinfo=UTC),
        entities=[
            {"name": "LPL", "type": "league", "role": "context"},
            {"name": "NIP", "type": "team", "role": "core"},
            {"name": "WBG", "type": "team", "role": "core"},
        ],
        primary_topic="esports",
        subtopic="match_schedule",
        product_scope="lol_esports",
        facets={"temporal": {"event_date": "2026-07-30"}},
    )
    event = create_event(
        db,
        normalized_item_id=schedule.id,
        aggregation_key="matchday:lpl:2026-07-30",
        title="LPL 7月30日比赛日",
        summary="NIP 对阵 WBG。",
        event_kind="esports_match",
        aggregation_strategy="calendar_day",
        product_scope="lol_esports",
    )
    game_result = _add_item(
        db,
        source_id=commentator.id,
        index=141,
        title="NIP 1-0 WBG",
        published_at=datetime(2026, 7, 30, 10, tzinfo=UTC),
        entities=[
            {"name": "NIP", "type": "team", "role": "core"},
            {"name": "WBG", "type": "team", "role": "core"},
        ],
        primary_topic="esports",
        subtopic="match_result",
        information_stage="result",
        product_scope="lol_esports",
        facets={"temporal": {"event_date": "2026-07-30"}},
    )

    routes = aggregation_routes(game_result)
    candidates = find_event_candidates(db, normalized_item_id=game_result.id)
    resolved = resolve_aggregation_routes(routes, candidates)

    assert routes[0].aggregation_key == "matchday:lpl:2026-07-30"
    assert candidates[0].event_id == event.id
    assert candidates[0].aggregation_key == routes[0].aggregation_key
    assert resolved[0].aggregation_key == "matchday:lpl:2026-07-30"
    assert resolved[0].aggregation_strategy == "calendar_day"


def test_match_identity_uses_date_and_unordered_team_pair_without_league(
    db: Session,
) -> None:
    source = Source(name="Regional matches", connector_type="manual")
    db.add(source)
    db.commit()
    common = {
        "source_id": source.id,
        "published_at": datetime(2026, 8, 8, tzinfo=UTC),
        "primary_topic": "esports",
        "subtopic": "match_result",
        "product_scope": "lol_esports",
        "facets": {"temporal": {"event_date": "2026-08-08"}},
    }
    first = _add_item(
        db,
        index=121,
        title="Alpha 2:1 Beta",
        entities=[
            {"name": "Alpha", "type": "team", "role": "core"},
            {"name": "Beta", "type": "team", "role": "core"},
        ],
        **common,
    )
    reversed_order = _add_item(
        db,
        index=122,
        title="Beta 1:2 Alpha",
        entities=[
            {"name": "Beta", "type": "team", "role": "core"},
            {"name": "Alpha", "type": "team", "role": "core"},
        ],
        **common,
    )

    assert _stable_key(first) == "match:2026-08-08:alpha-vs-beta"
    assert _stable_key(reversed_order) == _stable_key(first)


def test_routes_cover_tft_patch_free_rotation_and_merch(db: Session) -> None:
    source = Source(name="Coverage source", connector_type="tencent_lol")
    db.add(source)
    db.commit()
    tft_patch = _add_item(
        db,
        source_id=source.id,
        index=121,
        title="17.7 云顶之弈版本更新公告",
        published_at=datetime(2026, 7, 17, tzinfo=UTC),
        primary_topic="tft",
        subtopic="tft_patch",
        product_scope="tft",
    )
    rotation = _add_item(
        db,
        source_id=source.id,
        index=122,
        title="7月17日周免英雄更新公告",
        published_at=datetime(2026, 7, 17, tzinfo=UTC),
        primary_topic="commerce",
        subtopic="free_rotation",
        product_scope="lol_pc",
    )
    merch = _add_item(
        db,
        source_id=source.id,
        index=123,
        title="经典收藏礼盒开启预售",
        published_at=datetime(2026, 7, 17, tzinfo=UTC),
        entities=[
            {
                "name": "经典收藏礼盒",
                "type": "product",
                "role": "core",
            }
        ],
        primary_topic="commerce",
        subtopic="merch",
        product_scope="lol_merch_music",
    )

    assert _stable_key(tft_patch) == "patch:tft:17.7"
    assert _stable_key(rotation) is None
    assert _stable_key(merch) == "merch:lol_merch_music:经典收藏礼盒"
    assert aggregation_routes(merch)[0].event_kind == "commercial_offer"


def test_hotfix_identity_is_the_update_batch_not_the_first_target(
    db: Session,
) -> None:
    source = Source(name="Hotfix", connector_type="tencent_lol")
    db.add(source)
    db.commit()
    item = _add_item(
        db,
        source_id=source.id,
        index=124,
        title="8月6日不停机更新：佛耶戈、茂凯与贝蕾亚修复",
        published_at=datetime(2026, 8, 6, tzinfo=UTC),
        entities=[
            {"name": name, "type": "champion", "role": "affected"}
            for name in ("佛耶戈", "茂凯", "贝蕾亚")
        ],
        primary_topic="patch",
        subtopic="hotfix",
        information_stage="active",
        facets={"temporal": {"event_date": "2026-08-06"}},
    )

    assert _stable_key(item) == "hotfix:lol_pc:2026-08-06"


def test_explicit_dated_hotfixes_do_not_continue_across_dates(
    db: Session,
) -> None:
    official = Source(name="LOL CN", connector_type="tencent_lol", is_official=True)
    db.add(official)
    db.commit()
    first_update = _add_item(
        db,
        source_id=official.id,
        index=136,
        title="8月5日不停机更新修复佛耶戈",
        published_at=datetime(2026, 8, 5, 10, tzinfo=UTC),
        entities=[{"name": "佛耶戈", "type": "champion", "role": "affected"}],
        primary_topic="patch",
        subtopic="hotfix",
        information_stage="active",
        facets={"temporal": {"event_date": "2026-08-05"}},
    )
    event = create_event(
        db,
        normalized_item_id=first_update.id,
        aggregation_key="hotfix:lol_pc:2026-08-05",
        title="8月5日不停机更新",
        summary="修复佛耶戈。",
        event_kind="gameplay_update",
        aggregation_strategy="timeline",
        product_scope="lol_pc",
    )
    announcement = _add_item(
        db,
        source_id=official.id,
        index=137,
        title="8月6日不停机更新公告",
        published_at=datetime(2026, 8, 6, 8, tzinfo=UTC),
        entities=[
            {"name": "英雄联盟", "type": "product", "role": "core"},
            {"name": "佛耶戈", "type": "champion", "role": "affected"},
            {"name": "茂凯", "type": "champion", "role": "affected"},
        ],
        primary_topic="patch",
        subtopic="hotfix",
        information_stage="active",
        facets={"temporal": {"event_date": "2026-08-06"}},
    )

    candidates = find_event_candidates(db, normalized_item_id=announcement.id)
    dated_candidate = next(candidate for candidate in candidates if candidate.event_id == event.id)

    assert dated_candidate.deterministic_route_key is None
    assert (
        resolve_aggregation_routes(
            aggregation_routes(announcement),
            candidates,
        )[0].aggregation_key
        == "hotfix:lol_pc:2026-08-06"
    )


def test_nonstop_update_keeps_hotfix_as_the_message_level_identity(
    db: Session,
) -> None:
    source = Source(name="Mode update", connector_type="tencent_lol")
    db.add(source)
    db.commit()
    mentions = normalize_event_mentions(
        [
            {
                "topic": "game_mode",
                "subtopic": "game_mode_update",
                "identity_entities": [{"name": name, "type": "game_mode", "role": "core"}],
                "assertion": "asserted",
                "temporal": {"event_date": "2026-07-21"},
                "membership_role": "primary" if index == 0 else "component",
            }
            for index, name in enumerate(("斗魂竞技场", "海克斯大乱斗"))
        ]
    )
    item = _add_item(
        db,
        source_id=source.id,
        index=125,
        title="7月21日不停机模式更新公告",
        published_at=datetime(2026, 7, 21, tzinfo=UTC),
        primary_topic="game_mode",
        subtopic="game_mode_update",
        facets={"event_mentions": mentions},
    )

    route = aggregation_routes(item)[0]

    assert route.aggregation_key == "hotfix:lol_pc:2026-07-21"
    assert route.aggregation_strategy == "timeline"


def test_multiple_mode_changes_without_hotfix_signal_form_one_batch(
    db: Session,
) -> None:
    source = Source(name="Mode update", connector_type="tencent_lol")
    db.add(source)
    db.commit()
    mentions = normalize_event_mentions(
        [
            {
                "topic": "game_mode",
                "subtopic": "game_mode_update",
                "identity_entities": [{"name": name, "type": "game_mode", "role": "core"}],
                "assertion": "asserted",
                "temporal": {"event_date": "2026-07-21"},
                "membership_role": "primary" if index == 0 else "component",
            }
            for index, name in enumerate(("斗魂竞技场", "海克斯大乱斗"))
        ]
    )
    item = _add_item(
        db,
        source_id=source.id,
        index=126,
        title="7月21日模式平衡调整汇总",
        published_at=datetime(2026, 7, 21, tzinfo=UTC),
        primary_topic="game_mode",
        subtopic="game_mode_update",
        facets={"event_mentions": mentions},
    )

    assert _stable_key(item) == "gameplay_update_batch:lol_pc:2026-07-21"


def test_patch_cluster_absorbs_component_gameplay_updates(db: Session) -> None:
    source = Source(name="Patch preview", connector_type="x_twitter")
    db.add(source)
    db.commit()
    item = _add_item(
        db,
        source_id=source.id,
        index=135,
        title="26.15版本更新概览",
        published_at=datetime(2026, 7, 30, 16, tzinfo=UTC),
        primary_topic="patch",
        subtopic="patch_preview",
        facets={
            "event_mentions": normalize_event_mentions(
                [
                    {
                        "topic": "patch",
                        "subtopic": "patch_preview",
                        "identity_entities": [{"name": "26.15", "type": "patch", "role": "core"}],
                        "assertion": "asserted",
                        "temporal": {"event_date": None},
                        "membership_role": "primary",
                    },
                    *[
                        {
                            "topic": "champion",
                            "subtopic": "champion_update",
                            "identity_entities": [
                                {"name": name, "type": "champion", "role": "core"}
                            ],
                            "assertion": "asserted",
                            "temporal": {"event_date": None},
                            "membership_role": "component",
                        }
                        for name in ("卑尔维斯", "洛克")
                    ],
                ]
            )
        },
    )

    assert [route.aggregation_key for route in aggregation_routes(item)] == ["patch:lol_pc:26.15"]


def test_two_mentions_of_one_activity_keep_the_named_activity_identity(
    db: Session,
) -> None:
    source = Source(name="Activity stages", connector_type="tencent_lol")
    db.add(source)
    db.commit()
    item = _add_item(
        db,
        source_id=source.id,
        index=126,
        title="战斗之夜活动开放皮肤领取",
        published_at=datetime(2026, 8, 2, tzinfo=UTC),
        primary_topic="activity",
        subtopic="in_game_activity",
        facets={
            "event_mentions": normalize_event_mentions(
                [
                    {
                        "topic": "activity",
                        "subtopic": subtopic,
                        "identity_entities": [
                            {"name": "战斗之夜", "type": "activity", "role": "core"}
                        ],
                        "assertion": "asserted",
                        "temporal": {"event_date": "2026-08-02"},
                        "membership_role": role,
                    }
                    for subtopic, role in (
                        ("in_game_activity", "primary"),
                        ("free_reward", "component"),
                    )
                ]
            )
        },
    )

    assert [route.aggregation_key for route in aggregation_routes(item)] == [
        "activity:lol_pc:战斗之夜"
    ]


def test_free_reward_reminder_routes_to_named_parent_activity(db: Session) -> None:
    source = Source(name="Reward reminder", connector_type="baidu_tieba")
    db.add(source)
    db.commit()
    reminder = _add_item(
        db,
        source_id=source.id,
        index=124,
        title="战斗之夜皮肤现已开放领取",
        published_at=datetime(2026, 8, 2, tzinfo=UTC),
        entities=[
            {
                "name": "战斗之夜",
                "type": "activity",
                "role": "core",
            },
            {
                "name": "随机皮肤",
                "type": "skin",
                "role": "affected",
            },
        ],
        primary_topic="activity",
        subtopic="free_reward",
        information_stage="reminder",
    )

    route = aggregation_routes(reminder)[0]
    assert route.event_kind == "player_activity"
    assert route.aggregation_strategy == "timeline"
    assert route.aggregation_key == "activity:lol_pc:战斗之夜"
    assert route.creation_policy == "existing_only"


def test_named_activity_reminder_matches_a_unique_qualified_parent(
    db: Session,
) -> None:
    official = Source(name="Activity official", connector_type="tencent_lol")
    community = Source(name="Activity reminder", connector_type="baidu_tieba")
    db.add_all([official, community])
    db.commit()
    announcement = _add_item(
        db,
        source_id=official.id,
        index=138,
        title="经典战斗之夜活动公布",
        published_at=datetime(2026, 7, 30, tzinfo=UTC),
        entities=[{"name": "经典·战斗之夜", "type": "activity", "role": "core"}],
        primary_topic="activity",
        subtopic="in_game_activity",
        information_stage="announcement",
    )
    event = create_event(
        db,
        normalized_item_id=announcement.id,
        aggregation_key="activity:lol_pc:经典-战斗之夜",
        title="经典·战斗之夜活动公布",
        summary="8月2日可领取皮肤。",
        event_kind="player_activity",
        aggregation_strategy="timeline",
        product_scope="lol_pc",
    )
    reminder = _add_item(
        db,
        source_id=community.id,
        index=139,
        title="战斗之夜皮肤开箱开始",
        published_at=datetime(2026, 8, 2, tzinfo=UTC),
        entities=[{"name": "战斗之夜", "type": "activity", "role": "core"}],
        primary_topic="activity",
        subtopic="free_reward",
        information_stage="reminder",
    )

    candidates = find_event_candidates(db, normalized_item_id=reminder.id)

    assert candidates[0].event_id == event.id
    assert candidates[0].deterministic_route_key == "activity:lol_pc:战斗之夜"
    assert (
        resolve_aggregation_routes(aggregation_routes(reminder), candidates)[0].aggregation_key
        == "activity:lol_pc:经典-战斗之夜"
    )
    assert any(reason.startswith("命名主体包含：") for reason in candidates[0].reasons)


def test_data_mined_pbe_change_uses_patch_cycle_not_source_kind_route(
    db: Session,
) -> None:
    source = Source(name="PBE source", connector_type="baidu_tieba")
    db.add(source)
    db.commit()
    item = _add_item(
        db,
        source_id=source.id,
        index=124,
        title="26.16 测试服英雄改动",
        published_at=datetime(2026, 8, 5, tzinfo=UTC),
        entities=[
            {"name": "26.16", "type": "patch", "role": "context"},
            {"name": "卑尔维斯", "type": "champion", "role": "core"},
        ],
        primary_topic="patch",
        subtopic="pbe_change",
        source_kind="data_mined",
        information_stage="preview",
        product_scope="lol_pc",
    )

    routes = aggregation_routes(item)

    assert [(route.aggregation_strategy, route.aggregation_key) for route in routes] == [
        ("patch_cycle", "patch:lol_pc:26.16")
    ]


def test_ticketing_route_does_not_use_publication_date(db: Session) -> None:
    source = Source(name="Ticketing source", connector_type="x_twitter")
    db.add(source)
    db.commit()
    item = _add_item(
        db,
        source_id=source.id,
        index=125,
        title="Worlds ticketing update",
        published_at=datetime(2026, 8, 5, tzinfo=UTC),
        entities=[
            {"name": "Worlds", "type": "tournament", "role": "core"},
        ],
        primary_topic="esports",
        subtopic="ticketing",
        product_scope="lol_esports",
        facets={"temporal": {"event_date": None}},
    )

    assert aggregation_routes(item) == []
    item.facets = {"temporal": {"event_date": "2026-08-09"}}
    assert _stable_key(item) == "ticketing:worlds:2026-08-09"


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
        primary_topic="patch",
        subtopic="patch_notes",
        source_kind="first_party",
        information_stage="active",
    )
    db.add(
        Claim(
            normalized_item_id=patch.id,
            subject={"name": "26.15", "type": "patch"},
            predicate="adds_mode",
            object_value={"mode": {"name": "经典模式", "type": "game_mode"}},
            temporal_role="event",
            extraction_model="test",
        )
    )
    db.commit()
    roster = _add_item(
        db,
        source_id=source.id,
        index=14,
        title="传闻：WBG 正在考虑新的打野候选",
        published_at=datetime(2026, 7, 31, tzinfo=UTC),
        entities=[
            {"name": "WBG", "type": "team", "role": "affected"},
            {"name": "Xiaohou", "type": "player", "role": "core"},
        ],
        primary_topic="roster",
        subtopic="roster_move",
        source_kind="attributed_report",
        information_stage="rumor",
        product_scope="lol_esports",
    )
    regular = _add_item(
        db,
        source_id=source.id,
        index=15,
        title="LPL 常规赛赛果",
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
        entities=[{"name": "LPL", "type": "league", "role": "core"}],
        primary_topic="esports",
        subtopic="match_result",
        product_scope="lol_esports",
        facets={"temporal": {"event_date": "2026-08-01"}},
    )
    final = _add_item(
        db,
        source_id=source.id,
        index=16,
        title="LPL 总决赛赛果",
        published_at=datetime(2026, 8, 2, tzinfo=UTC),
        entities=[
            {"name": "LPL", "type": "tournament", "role": "core"},
            {"name": "BLG", "type": "team", "role": "affected"},
            {"name": "TES", "type": "team", "role": "affected"},
        ],
        primary_topic="esports",
        subtopic="match_result",
        product_scope="lol_esports",
        facets={"temporal": {"event_date": "2026-08-02"}},
    )
    final.importance_dimensions = {"prominence": {"value": "star"}}
    db.commit()

    patch_routes = aggregation_routes(patch)
    assert [
        (route.event_kind, route.aggregation_key, route.membership_role) for route in patch_routes
    ] == [
        ("gameplay_update", "patch:lol_pc:26.15", "primary"),
        ("gameplay_release", "gameplay:lol_pc:经典模式", "component"),
    ]
    assert [(route.event_kind, route.aggregation_key) for route in aggregation_routes(roster)] == [
        ("roster_change", "transfer:2026:xiaohou")
    ]
    assert [(route.event_kind, route.aggregation_key) for route in aggregation_routes(regular)] == [
        ("esports_match", "matchday:lpl:2026-08-01")
    ]
    assert aggregation_routes(final)[0].event_kind == "esports_match"
    assert aggregation_routes(final)[0].aggregation_strategy == "timeline"


def test_event_identity_normalization_is_domain_general(db: Session) -> None:
    entities = normalize_entities(
        [
            {
                "name": "NIP电子竞技俱乐部",
                "type": "team",
                "canonical_name": "NIP电子竞技俱乐部",
                "role": "core",
            },
            {
                "name": "2026LPL第三赛段",
                "type": "league",
                "canonical_name": "2026LPL第三赛段",
                "role": "context",
            },
            {
                "name": "圣枪哥",
                "type": "player",
                "canonical_name": "Flandre（圣枪哥）",
                "role": "core",
            },
        ]
    )

    assert [entity["canonical_name"] for entity in entities] == [
        "NIP",
        "lpl",
        "Flandre",
    ]
    assert [entity["canonical_id"] for entity in entities] == [
        "team:nip",
        "league:lpl",
        "player:flandre",
    ]
    aram_aliases = [
        normalize_entities(
            [{"name": alias, "type": "game_mode", "role": "core"}]
        )[0]["canonical_name"]
        for alias in ("ARAM：混乱", "极地大乱斗：混乱模式", "海克斯大乱斗")
    ]
    assert aram_aliases == ["ARAM Mayhem"] * 3


def test_event_mentions_cluster_regular_matches_and_route_transfer_subjects(
    db: Session,
) -> None:
    source = Source(name="Multi-event source", connector_type="weibo")
    db.add(source)
    db.commit()
    match_mentions = normalize_event_mentions(
        [
            {
                "topic": "esports",
                "subtopic": "match_schedule",
                "identity_entities": [
                    {"name": left, "type": "team", "canonical_name": left, "role": "core"},
                    {"name": right, "type": "team", "canonical_name": right, "role": "core"},
                ],
                "assertion": "asserted",
                "temporal": {"event_date": "2026-07-30"},
                "membership_role": "primary" if index == 0 else "component",
            }
            for index, (left, right) in enumerate(
                (("AL", "EDG"), ("GEN", "T1"), ("WE", "BLG"))
            )
        ]
    )
    schedule = _add_item(
        db,
        source_id=source.id,
        index=102,
        title="今日赛程：AL vs EDG、GEN vs T1、WE vs BLG，GEN vs T1为焦点战",
        published_at=datetime(2026, 7, 29, tzinfo=UTC),
        primary_topic="esports",
        subtopic="match_schedule",
        product_scope="lol_esports",
        facets={"event_mentions": match_mentions},
    )

    assert [route.aggregation_key for route in aggregation_routes(schedule)] == [
        "matchday:lpl:2026-07-30",
        "matchday:lck:2026-07-30",
    ]

    roster = _add_item(
        db,
        source_id=source.id,
        index=103,
        title="BLG阵容选择出现多个进展",
        published_at=datetime(2026, 7, 31, tzinfo=UTC),
        primary_topic="roster",
        subtopic="roster_move",
        product_scope="lol_esports",
        facets={
            "event_mentions": normalize_event_mentions(
                [
                    {
                        "topic": "roster",
                        "subtopic": "roster_move",
                        "identity_entities": [
                            {
                                "name": "圣枪哥",
                                "type": "player",
                                "canonical_name": "Flandre",
                                "role": "core",
                            }
                        ],
                        "assertion": "speculative",
                        "temporal": {"event_date": None},
                        "membership_role": "primary",
                    },
                    {
                        "topic": "roster",
                        "subtopic": "roster_move",
                        "identity_entities": [
                            {
                                "name": "Bin",
                                "type": "player",
                                "canonical_name": "Bin",
                                "role": "core",
                            }
                        ],
                        "assertion": "context_only",
                        "temporal": {"event_date": None},
                        "membership_role": "cross_ref",
                    },
                ]
            )
        },
    )
    roster_routes = {route.aggregation_key: route for route in aggregation_routes(roster)}
    assert set(roster_routes) == {
        "transfer:2026:flandre",
        "transfer:2026:bin",
    }
    assert roster_routes["transfer:2026:flandre"].creation_policy == "allow"
    assert roster_routes["transfer:2026:bin"].creation_policy == "existing_only"


def test_negated_release_can_only_join_an_existing_event(db: Session) -> None:
    source = Source(name="Concept source", connector_type="manual")
    db.add(source)
    db.commit()
    item = _add_item(
        db,
        source_id=source.id,
        index=101,
        title="概念皮肤不会上线",
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
        entities=[{"name": "概念皮肤", "type": "skin", "role": "core"}],
        primary_topic="skin",
        subtopic="skin_release",
        facets={"event_assertion": "negated"},
    )

    route = aggregation_routes(item)[0]
    assert route.aggregation_key == "release:lol_pc:概念皮肤"
    assert route.creation_policy == "existing_only"


def test_repost_can_only_supplement_an_existing_event(db: Session) -> None:
    source = Source(name="Social repost", connector_type="weibo")
    db.add(source)
    db.commit()
    item = _add_item(
        db,
        source_id=source.id,
        index=102,
        title="转发玩家同人作品",
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
        entities=[{"name": "剪纸仙灵", "type": "skin", "role": "core"}],
        primary_topic="skin",
        subtopic="skin_release",
        content_form="repost",
    )

    route = aggregation_routes(item)[0]

    assert route.aggregation_key == "release:lol_pc:剪纸仙灵"
    assert route.creation_policy == "existing_only"


def test_tft_component_uses_its_own_product_scope(db: Session) -> None:
    source = Source(name="Mixed product announcement", connector_type="tencent_lol")
    db.add(source)
    db.commit()
    mentions = normalize_event_mentions(
        [
            {
                "topic": "activity",
                "subtopic": "in_game_activity",
                "identity_entities": [{"name": "暑期活动", "type": "activity", "role": "core"}],
                "assertion": "asserted",
                "temporal": {"event_date": "2026-07-16"},
                "membership_role": "primary",
            },
            {
                "topic": "game_mode",
                "subtopic": "game_mode_release",
                "identity_entities": [
                    {"name": "恭喜发财", "type": "game_mode", "role": "core"},
                    {"name": "云顶之弈", "type": "game_mode", "role": "context"},
                ],
                "assertion": "asserted",
                "temporal": {"event_date": "2026-07-16"},
                "membership_role": "component",
            },
        ]
    )
    item = _add_item(
        db,
        source_id=source.id,
        index=103,
        title="暑期活动与云顶之弈模式上线",
        published_at=datetime(2026, 7, 16, tzinfo=UTC),
        entities=[
            {"name": "暑期活动", "type": "activity", "role": "core"},
            {"name": "恭喜发财", "type": "game_mode", "role": "core"},
            {"name": "云顶之弈", "type": "game_mode", "role": "context"},
        ],
        primary_topic="activity",
        subtopic="in_game_activity",
        product_scope="lol_pc",
        facets={"event_mentions": mentions},
    )

    routes = {route.aggregation_key: route for route in aggregation_routes(item)}

    assert routes["release:tft:恭喜发财"].product_scope == "tft"


def test_lpl_schedule_and_result_share_matchday_key_out_of_order(
    db: Session,
) -> None:
    source = Source(name="LPL Matchday", connector_type="weibo")
    db.add(source)
    db.commit()
    teams = [
        {"name": "LPL", "type": "league"},
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
        primary_topic="esports",
        subtopic="match_result",
        product_scope="lol_esports",
        facets={"temporal": {"event_date": "2026-07-26"}},
    )
    schedule = _add_item(
        db,
        source_id=source.id,
        index=21,
        title="2026LPL第三赛段7月26日赛程预告",
        published_at=datetime(2026, 7, 26, 6, 11, tzinfo=UTC),
        entities=teams,
        primary_topic="esports",
        subtopic="match_schedule",
        product_scope="lol_esports",
        facets={"temporal": {"event_date": "2026-07-26"}},
    )

    key = "matchday:lpl:2026-07-26"
    assert _stable_key(result) == key
    assert _stable_key(schedule) == key
    event = create_event(
        db,
        normalized_item_id=result.id,
        aggregation_key=key,
        title="2026LPL第三赛段7月26日赛果",
        summary="当日三场比赛已经结束。",
        event_kind="esports_match",
        aggregation_strategy="calendar_day",
        product_scope="lol_esports",
        lifecycle_status="completed",
    )

    candidates = find_event_candidates(db, normalized_item_id=schedule.id)
    assert candidates[0].event_id == event.id
    assert candidates[0].aggregation_key == key
    assert candidates[0].score >= 100
    assert "发布时间相距 0 天" in candidates[0].reasons


def test_marquee_match_uses_stage_competition_and_structured_prominence(
) -> None:
    assert not is_marquee_match("LPL常规赛焦点战", "lpl", "star")
    assert not is_marquee_match("LPL季后赛半决赛", "lpl", "normal")
    assert is_marquee_match("LPL季后赛半决赛", "lpl", "star")
    assert is_marquee_match("MSI瑞士轮", "msi", "notable")
    assert is_marquee_match("全球总决赛八强赛", "worlds", "normal")


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
        aggregation_key="patch:lol_pc:26.13",
        title="英雄联盟 26.13 版本预览",
        summary="初始预览",
        event_kind="gameplay_update",
        aggregation_strategy="patch_cycle",
        product_scope="lol_pc",
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
    assert any("稳定聚合键匹配" in reason for reason in first[0].reasons)


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
        primary_topic="esports",
        subtopic="match_result",
        product_scope="lol_esports",
    )
    create_event(
        db,
        normalized_item_id=existing.id,
        title="一月职业联赛",
        summary="赛果",
        event_kind="esports_match",
        aggregation_strategy="calendar_day",
        product_scope="lol_esports",
    )
    incoming = _add_item(
        db,
        source_id=source.id,
        index=2,
        title="全新皮肤上线",
        published_at=datetime(2026, 7, 1, tzinfo=UTC),
        entities=[{"name": "Champion B", "type": "champion"}],
        primary_topic="skin",
        subtopic="skin_release",
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
            title=f"26.{index + 1} 阿狸平衡调整",
            published_at=base_time - timedelta(days=index),
            entities=[{"name": "阿狸", "type": "champion"}],
            primary_topic="patch",
            subtopic="patch_preview",
        )
        create_event(
            db,
            normalized_item_id=member.id,
            title=f"阿狸平衡调整事件 {index}",
            summary="测试",
            event_kind="gameplay_update",
            aggregation_strategy="timeline",
            product_scope="lol_pc",
        )
    incoming = _add_item(
        db,
        source_id=source.id,
        index=100,
        title="26.99 阿狸平衡调整后续",
        published_at=base_time,
        entities=[{"name": "阿狸", "type": "champion"}],
        primary_topic="patch",
        subtopic="patch_preview",
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
        primary_topic="community",
        subtopic="community_event",
    )
    event = create_event(
        db,
        normalized_item_id=old.id,
        title="艺术家长廊开放申请",
        summary="开放申请。",
        event_kind="community_activity",
        aggregation_strategy="singleton",
        product_scope="lol_pc",
    )
    new = _add_item(
        db,
        source_id=source.id,
        index=201,
        title="完全不同的页面标题",
        published_at=datetime(2026, 9, 22, tzinfo=UTC),
        primary_topic="community",
        subtopic="community_event",
        revision=2,
        supersedes_raw_item_id=old.raw_item_id,
    )
    old.publication_status = "superseded"
    event.messages[0].membership_status = "withdrawn"
    event.status = "withdrawn"
    db.commit()

    candidates = find_event_candidates(db, normalized_item_id=new.id)

    assert candidates[0].event_id == event.id
    assert candidates[0].score >= 200
    assert "当前消息是该事件成员的新修订" in candidates[0].reasons


def test_game_mode_candidate_survives_title_drift_and_reverse_time_order(
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
                "role": "core",
            }
        ],
        primary_topic="game_mode",
        subtopic="game_mode_release",
    )
    event = create_event(
        db,
        normalized_item_id=later_announcement.id,
        title="英雄联盟经典模式将于7月30日上线",
        summary="官方公布经典模式上线日期。",
        event_kind="gameplay_release",
        aggregation_strategy="release",
        product_scope="lol_pc",
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
                "role": "core",
            }
        ],
        primary_topic="game_mode",
        subtopic="game_mode_release",
    )

    candidates = find_event_candidates(
        db,
        normalized_item_id=earlier_reveal.id,
    )

    assert candidates[0].event_id == event.id
    assert any("事件事实类型一致" in reason for reason in candidates[0].reasons)
    assert any("相距 10 天" in reason for reason in candidates[0].reasons)


def test_stable_release_subject_alias_resolves_before_model_decision(
    db: Session,
) -> None:
    source = Source(name="Mode Alias", connector_type="riot_official")
    db.add(source)
    db.commit()
    earlier = _add_item(
        db,
        source_id=source.id,
        index=302,
        title="经典模式首次公开",
        published_at=datetime(2026, 7, 14, tzinfo=UTC),
        entities=[{"name": "经典模式", "type": "game_mode", "role": "core"}],
        primary_topic="game_mode",
        subtopic="game_mode_release",
    )
    event = create_event(
        db,
        normalized_item_id=earlier.id,
        aggregation_key="release:lol_pc:经典模式",
        title="经典模式首次公开",
        summary="经典模式首次公开。",
        event_kind="gameplay_release",
        aggregation_strategy="release",
        product_scope="lol_pc",
    )
    later = _add_item(
        db,
        source_id=source.id,
        index=303,
        title="英雄联盟经典正式公布",
        published_at=datetime(2026, 7, 15, tzinfo=UTC),
        entities=[{"name": "英雄联盟经典", "type": "game_mode", "role": "core"}],
        primary_topic="game_mode",
        subtopic="game_mode_release",
    )

    routes = aggregation_routes(later)
    candidates = find_event_candidates(db, normalized_item_id=later.id)

    assert candidates[0].event_id == event.id
    assert candidates[0].deterministic_route_key == "release:lol_pc:英雄联盟经典"
    assert resolve_aggregation_routes(routes, candidates)[0].aggregation_key == (
        "release:lol_pc:经典模式"
    )


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
        primary_topic="game_mode",
        subtopic="game_mode_release",
    )
    create_event(
        db,
        normalized_item_id=reveal.id,
        aggregation_key="release:lol_pc:classic-mode",
        title="拳头宣布推出英雄联盟经典模式",
        summary="经典模式将重现早期英雄联盟玩法。",
        event_kind="gameplay_release",
        aggregation_strategy="release",
        product_scope="lol_pc",
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
        primary_topic="commerce",
        subtopic="shop_offer",
        information_stage="preview",
    )

    candidates = find_event_candidates(
        db,
        normalized_item_id=dependent_asset.id,
    )

    assert candidates == []


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
        primary_topic="commerce",
        subtopic="shop_rotation",
        information_stage="update",
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
        primary_topic="commerce",
        subtopic="shop_rotation",
        information_stage="update",
    )

    assert _stable_key(weekly) == "shop_rotation:lol_pc:cn:2026-W30"
    assert _stable_key(daily) == "shop_rotation:lol_pc:cn:2026-W30"

    event = create_event(
        db,
        normalized_item_id=weekly.id,
        aggregation_key=_stable_key(weekly),
        title="2026年第30周国服神话商城轮换",
        summary="本周神话商城进行轮换。",
        event_kind="commercial_offer",
        aggregation_strategy="recurring_window",
        product_scope="lol_pc",
    )
    candidates = find_event_candidates(db, normalized_item_id=daily.id)

    assert candidates[0].event_id == event.id
    assert candidates[0].match_level == "strong"
    assert any("稳定聚合键匹配" in reason for reason in candidates[0].reasons)


def test_cn_mythic_shop_key_includes_iso_year(db: Session) -> None:
    source = Source(name="国服商城跨年观察", connector_type="baidu_tieba")
    db.add(source)
    db.commit()
    first_year = _add_item(
        db,
        source_id=source.id,
        index=505,
        title="神话商城每周轮换",
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        primary_topic="commerce",
        subtopic="shop_rotation",
        information_stage="update",
    )
    next_year = _add_item(
        db,
        source_id=source.id,
        index=506,
        title="神话商城每周轮换",
        published_at=datetime(2027, 1, 7, tzinfo=UTC),
        primary_topic="commerce",
        subtopic="shop_rotation",
        information_stage="update",
    )

    assert _stable_key(first_year) == "shop_rotation:lol_pc:cn:2026-W01"
    assert _stable_key(next_year) == "shop_rotation:lol_pc:cn:2027-W01"
    assert _stable_key(first_year) != _stable_key(next_year)


def test_x_mythic_shop_rotation_uses_global_week_identity(db: Session) -> None:
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
        primary_topic="commerce",
        subtopic="shop_rotation",
    )

    assert _stable_key(item) == "shop_rotation:lol_pc:global:2026-W30"
