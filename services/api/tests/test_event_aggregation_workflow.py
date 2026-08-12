from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.domain.event_admission import decide_event_admission
from app.domain.event_categories import event_category
from app.domain.event_families import (
    canonicalize_event_anchors,
    has_complete_mythic_shop_identity,
)
from app.domain.event_granularity import is_daily_match_roundup
from app.models.event import Event, EventAggregationRun, EventMention
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.schemas.event_aggregation import EventAggregationResult
from app.services.event_candidates import recall_event_candidates
from app.services.events import create_event
from app.services.llm import LLMAnalysisError
from app.workflows.event_aggregation import _select_content, aggregate_normalized_item
from app.workflows.event_aggregation import _aggregation_key


def _item(
    db: Session,
    *,
    source: Source,
    external_id: str,
    title: str,
    message_type: str,
    topics: list[str],
    entities: list[dict[str, object]],
    content_form: str = "original",
    products: list[str] | None = None,
) -> NormalizedItem:
    raw = RawItem(
        source_id=source.id,
        external_id=external_id,
        native_title=title,
        canonical_url=f"https://example.com/{external_id}",
        content_blocks=[{"type": "paragraph", "text": title}],
        published_at=datetime.now(UTC),
    )
    db.add(raw)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw.id,
        normalized_title=title,
        normalized_text=title,
        summary=title,
        entities=entities,
        products=products or ["lol_pc"],
        message_type=message_type,
        topics=topics,
        content_form=content_form,
        importance_score=0.5,
        importance_calculation={
            "importance_profile": "gameplay_announcement",
            "profile_score": 0.5,
            "final_score": 0.5,
        },
        translated_title=title,
        translated_text=title,
        translated_content_blocks=[{"type": "paragraph", "text": title}],
        translation_status="not_required",
        analysis_model="test",
        analysis_version="test",
    )
    db.add(item)
    db.flush()
    return item


class ScenarioClient:
    def __init__(self) -> None:
        self.calls = 0

    async def aggregate_events(self, **payload: object) -> EventAggregationResult:
        self.calls += 1
        message = payload["message"]
        assert isinstance(message, dict)
        title = str(message["title"])
        candidates = payload["candidates"]
        assert isinstance(candidates, list)
        if title == "26.17 平衡爆料":
            mentions = [
                {
                    "mention_index": 0,
                    "event_family": "gameplay_balance",
                    "action": "create",
                    "candidate_event_id": None,
                    "relation": "reports",
                    "source_role": "known_leaker",
                    "materiality": "material_update",
                    "canonical_anchors": {"patch_version": "patch:26.17"},
                    "event_title": "26.17 版本平衡调整",
                    "proposed_summary": "爆料称 26.17 将进行平衡调整。",
                    "latest_development": "首次爆料",
                    "evidence_excerpt": "26.17 平衡爆料",
                }
            ]
        elif title == "星界活动爆料":
            mentions = [
                {
                    "mention_index": 0,
                    "event_family": "player_activity",
                    "action": "create",
                    "candidate_event_id": None,
                    "relation": "reports",
                    "source_role": "known_leaker",
                    "materiality": "material_update",
                    "canonical_anchors": {"activity_name": "activity:star"},
                    "event_title": "星界活动",
                    "proposed_summary": "爆料称星界活动将在下版本开放。",
                    "latest_development": "首次爆料",
                    "evidence_excerpt": "星界活动爆料",
                }
            ]
        else:
            by_family = {str(candidate["event_family"]): candidate for candidate in candidates}
            mentions = [
                {
                    "mention_index": 0,
                    "event_family": "gameplay_balance",
                    "action": "update",
                    "candidate_event_id": by_family["gameplay_balance"]["event_id"],
                    "relation": "confirms",
                    "source_role": "responsible_official",
                    "materiality": "material_update",
                    "canonical_anchors": {"patch_version": "patch:26.17"},
                    "event_title": "26.17 版本平衡调整",
                    "proposed_summary": "官网确认 26.17 版本平衡调整。",
                    "latest_development": "官网确认",
                    "evidence_excerpt": "官网确认平衡调整",
                },
                {
                    "mention_index": 1,
                    "event_family": "player_activity",
                    "action": "update",
                    "candidate_event_id": by_family["player_activity"]["event_id"],
                    "relation": "confirms",
                    "source_role": "responsible_official",
                    "materiality": "material_update",
                    "canonical_anchors": {"activity_name": "activity:star"},
                    "event_title": "星界活动",
                    "proposed_summary": "官网确认星界活动。",
                    "latest_development": "官网确认",
                    "evidence_excerpt": "官网确认活动",
                },
                {
                    "mention_index": 2,
                    "event_family": "cosmetic_release",
                    "action": "create",
                    "candidate_event_id": None,
                    "relation": "reports",
                    "source_role": "responsible_official",
                    "materiality": "material_update",
                    "canonical_anchors": {"skin_series": "skin:star-series"},
                    "event_title": "星界系列皮肤发布",
                    "proposed_summary": "官网公布星界系列皮肤。",
                    "latest_development": "官网首次公布",
                    "evidence_excerpt": "星界系列皮肤",
                },
            ]
        return EventAggregationResult.model_validate(
            {"mentions": mentions, "ignored_fragments": []}
        )


def test_admission_is_deterministic_and_can_avoid_model_calls() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name="Admission source")
        db.add(source)
        db.flush()
        unknown = _item(
            db,
            source=source,
            external_id="unknown",
            title="无语义",
            message_type="unknown",
            topics=["unknown"],
            entities=[],
            products=["unknown"],
            content_form="media_only",
        )
        repost = _item(
            db,
            source=source,
            external_id="repost",
            title="转载 26.17",
            message_type="game_leak",
            topics=["balance_gameplay"],
            entities=[{"type": "patch", "canonical_id": "patch:26.17"}],
            content_form="repost",
        )
        leak = _item(
            db,
            source=source,
            external_id="leak",
            title="26.17 爆料",
            message_type="game_leak",
            topics=["balance_gameplay"],
            entities=[{"type": "patch", "canonical_id": "patch:26.17"}],
        )

        assert decide_event_admission(unknown).decision == "skip"
        assert decide_event_admission(repost).decision == "update_existing_only"
        assert decide_event_admission(leak).decision == "create_or_update"


@pytest.mark.parametrize(
    ("title", "message_type", "topics", "expected"),
    [
        ("本周周免英雄名单", "game_community_notice", ["champions"], "skip"),
        ("This week's free champion rotation", "game_community_notice", ["champions"], "skip"),
        ("26.17 英雄平衡调整", "game_patch_notes", ["balance_gameplay"], "create_or_update"),
        ("新英雄发布", "game_announcement", ["champions"], "create_or_update"),
    ],
)
def test_free_champion_rotation_exclusion_is_narrow(
    title: str, message_type: str, topics: list[str], expected: str
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name=f"Admission {title}")
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id=title,
            title=title,
            message_type=message_type,
            topics=topics,
            entities=[{"type": "champion", "canonical_id": "champion:test"}],
        )
        assert decide_event_admission(item).decision == expected


@pytest.mark.anyio
async def test_free_champion_rotation_is_recorded_as_zero_call_skip() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name="Free rotation workflow source")
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id="free-rotation-workflow",
            title="This week's free champion rotation",
            message_type="game_community_notice",
            topics=["champions"],
            entities=[{"type": "champion", "canonical_id": "champion:test"}],
        )
        db.commit()

        class FailingClient:
            async def aggregate_events(self, **_payload: object) -> EventAggregationResult:
                raise AssertionError("free rotation must not call the event model")

        run = await aggregate_normalized_item(db, item, llm_client=FailingClient())

        assert run.outcome == "skipped_by_admission"
        assert run.admission_decision == "skip"
        assert run.model_call_count == 0
        assert "free champion rotation" in run.decision_draft["admission_reasons"][0]


def test_mythic_shop_identity_is_market_and_rotation_period() -> None:
    anchors = {
        "shop": "Mythic Shop",
        "market": "CN",
        "rotation": "2026 week 32",
        "products": ["skin:a", "chromas:b"],
    }
    assert canonicalize_event_anchors("commercial_offer", anchors) == {
        "shop": "mythic_shop",
        "market": "cn",
        "rotation_period": "2026-w32",
    }
    assert has_complete_mythic_shop_identity(
        "commercial_offer", {"shop": "mythic_shop", "market": "cn", "rotation_period": "2026-w32"}
    )
    same_rotation_different_products = _aggregation_key(
        event_family="commercial_offer",
        products=["lol_pc"],
        canonical_anchors=canonicalize_event_anchors("commercial_offer", anchors),
    )
    same_rotation_more_products = _aggregation_key(
        event_family="commercial_offer",
        products=["lol_pc", "tft"],
        canonical_anchors=canonicalize_event_anchors("commercial_offer", anchors),
    )
    next_rotation = _aggregation_key(
        event_family="commercial_offer",
        products=["lol_pc"],
        canonical_anchors={
            "shop": "mythic_shop",
            "market": "cn",
            "rotation_period": "2026-w33",
        },
    )
    overseas = _aggregation_key(
        event_family="commercial_offer",
        products=["lol_pc"],
        canonical_anchors={
            "shop": "mythic_shop",
            "market": "overseas",
            "rotation_period": "2026-w32",
        },
    )
    assert same_rotation_different_products == same_rotation_more_products
    assert same_rotation_different_products != next_rotation
    assert same_rotation_different_products != overseas


def test_daily_match_roundup_hint_excludes_schedule_summary() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name="Esports roundup")
        db.add(source)
        db.flush()
        roundup = _item(
            db,
            source=source,
            external_id="lck-roundup",
            title="今日 LCK 三场比赛 A vs B、C vs D、E vs F",
            message_type="esports_announcement",
            topics=["esports_schedule"],
            entities=[
                {"type": "team", "canonical_id": value}
                for value in ("team:a", "team:b", "team:c", "team:d", "team:e", "team:f")
            ],
            products=["lol_esports"],
        )
        postponed = _item(
            db,
            source=source,
            external_id="postponed-match",
            title="今日比赛延期：A vs B",
            message_type="esports_announcement",
            topics=["esports_schedule"],
            entities=[],
            products=["lol_esports"],
        )
        assert is_daily_match_roundup(roundup)
        assert not is_daily_match_roundup(postponed)


class DailyRoundupScheduleClient:
    async def aggregate_events(self, **payload: object) -> EventAggregationResult:
        message = payload["message"]
        assert isinstance(message, dict)
        assert "daily_esports_match_roundup" in message["editorial_granularity_guidance"]
        return EventAggregationResult.model_validate(
            {
                "mentions": [
                    {
                        "mention_index": 0,
                        "event_family": "esports_schedule",
                        "action": "create",
                        "relation": "reports",
                        "source_role": "responsible_official",
                        "materiality": "material_update",
                        "canonical_anchors": {"date": "2026-08-12"},
                        "event_title": "今日赛程汇总",
                        "proposed_summary": "今日比赛安排。",
                        "evidence_excerpt": "今日三场比赛",
                    }
                ]
            }
        )


class DailyMatchClient:
    async def aggregate_events(self, **_payload: object) -> EventAggregationResult:
        return EventAggregationResult.model_validate(
            {
                "mentions": [
                    {
                        "mention_index": index,
                        "event_family": "esports_match",
                        "action": "create",
                        "relation": "reports",
                        "source_role": "responsible_official",
                        "materiality": "material_update",
                        "canonical_anchors": {"match": f"match:{index}"},
                        "event_title": f"第 {index + 1} 场比赛",
                        "proposed_summary": f"第 {index + 1} 场比赛安排。",
                        "evidence_excerpt": f"第 {index + 1} 场",
                    }
                    for index in range(3)
                ]
            }
        )


@pytest.mark.anyio
async def test_three_match_roundup_creates_three_match_events_without_daily_event() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name="Three match roundup source", is_official=True)
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id="three-match-roundup",
            title="今日 LCK 三场比赛 A vs B、C vs D、E vs F",
            message_type="esports_announcement",
            topics=["esports_schedule"],
            entities=[],
            products=["lol_esports"],
        )
        db.commit()

        run = await aggregate_normalized_item(db, item, llm_client=DailyMatchClient())

        assert run.outcome == "applied"
        assert db.scalar(select(func.count(Event.id))) == 3
        assert {
            event.event_family for event in db.scalars(select(Event)).all()
        } == {"esports_match"}


@pytest.mark.anyio
async def test_daily_roundup_cannot_create_schedule_summary_event() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name="Roundup validator source", is_official=True)
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id="roundup-validator",
            title="今日 LEC 三场比赛 A vs B、C vs D、E vs F",
            message_type="esports_announcement",
            topics=["esports_schedule"],
            entities=[],
            products=["lol_esports"],
        )
        db.commit()
        with pytest.raises(ValueError, match="daily match reminders"):
            await aggregate_normalized_item(
                db, item, llm_client=DailyRoundupScheduleClient()
            )


@pytest.mark.parametrize("league", ["LPL", "LCK", "LEC"])
def test_daily_roundup_detection_applies_to_all_leagues(league: str) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name=f"{league} roundup source")
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id=f"{league}-roundup",
            title=f"今日 {league} 三场比赛 A vs B、C vs D、E vs F",
            message_type="esports_announcement",
            topics=["esports_schedule"],
            entities=[],
            products=["lol_esports"],
        )
        assert is_daily_match_roundup(item)


@pytest.mark.parametrize(
    ("family", "products", "expected"),
    [
        ("esports_schedule", ["lol_pc", "lol_esports"], "esports"),
        ("gameplay_release", ["lol_pc"], "lol_pc"),
        ("gameplay_release", ["tft"], "tft"),
        ("gameplay_release", ["other_lol_product"], "other_products"),
        ("corporate_change", ["riot_ecosystem"], "ecosystem"),
    ],
)
def test_event_category_mapping_is_centralized(
    family: str, products: list[str], expected: str
) -> None:
    assert event_category(event_family=family, products=products) == expected


def test_structured_output_rejects_nonmaterial_rewrites_and_bad_indexes() -> None:
    with pytest.raises(ValidationError, match="non-material mentions"):
        EventAggregationResult.model_validate(
            {
                "mentions": [
                    {
                        "mention_index": 0,
                        "event_family": "gameplay_balance",
                        "action": "update",
                        "candidate_event_id": 1,
                        "relation": "supports",
                        "source_role": "independent_media",
                        "materiality": "corroboration_only",
                        "evidence_excerpt": "重复报道",
                        "proposed_summary": "不允许改写",
                    }
                ]
            }
        )
    with pytest.raises(ValidationError, match="contiguous"):
        EventAggregationResult.model_validate(
            {
                "mentions": [
                    {
                        "mention_index": 1,
                        "event_family": "gameplay_balance",
                        "action": "ignore",
                        "relation": "mentions",
                        "source_role": "unknown",
                        "materiality": "context_only",
                    }
                ]
            }
        )
    duplicate_create = {
        "event_family": "gameplay_balance",
        "action": "create",
        "candidate_event_id": None,
        "relation": "reports",
        "source_role": "known_leaker",
        "materiality": "material_update",
        "canonical_anchors": {"patch_version": "patch:collision"},
        "event_title": "冲突事件",
        "proposed_summary": "相同身份只能创建一次。",
        "evidence_excerpt": "冲突",
    }
    with pytest.raises(ValidationError, match="must be merged"):
        EventAggregationResult.model_validate(
            {
                "mentions": [
                    {**duplicate_create, "mention_index": 0},
                    {**duplicate_create, "mention_index": 1},
                ]
            }
        )


def test_long_content_prefers_entity_relevant_translated_blocks() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name="Long article source")
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id="long",
            title="超长版本公告",
            message_type="game_patch_notes",
            topics=["balance_gameplay"],
            entities=[
                {
                    "type": "champion",
                    "name": "阿狸",
                    "canonical_id": "champion:ahri",
                }
            ],
        )
        item.translated_text = "无关段落" * 10_000 + "阿狸伤害调整"
        item.translated_content_blocks = [
            {"type": "paragraph", "text": "无关段落" * 7_000},
            {"type": "paragraph", "text": "阿狸伤害调整"},
        ]

        selected, metadata = _select_content(item)

        assert len(selected) <= 24_000
        assert "阿狸伤害调整" in selected
        assert metadata["content_truncated"] is True
        assert metadata["strategy"] == "entity_relevant_translated_blocks"


@pytest.mark.anyio
async def test_one_official_call_updates_two_events_and_creates_a_third() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        leaker = Source(name="Leaker", reliability_score=0.8)
        official = Source(name="Official", is_official=True, reliability_score=1)
        db.add_all([leaker, official])
        db.flush()
        balance_item = _item(
            db,
            source=leaker,
            external_id="balance",
            title="26.17 平衡爆料",
            message_type="game_leak",
            topics=["balance_gameplay"],
            entities=[{"type": "patch", "canonical_id": "patch:26.17"}],
        )
        activity_item = _item(
            db,
            source=leaker,
            external_id="activity",
            title="星界活动爆料",
            message_type="game_leak",
            topics=["activities_rewards"],
            entities=[{"type": "activity", "canonical_id": "activity:star"}],
        )
        official_item = _item(
            db,
            source=official,
            external_id="patch-notes",
            title="26.17 官网综合版本公告",
            message_type="game_patch_notes",
            topics=["balance_gameplay", "activities_rewards", "cosmetics"],
            entities=[
                {"type": "patch", "canonical_id": "patch:26.17"},
                {"type": "activity", "canonical_id": "activity:star"},
                {"type": "skin", "canonical_id": "skin:star-series"},
            ],
        )
        db.commit()
        client = ScenarioClient()

        await aggregate_normalized_item(db, balance_item, llm_client=client)
        await aggregate_normalized_item(db, activity_item, llm_client=client)
        official_run = await aggregate_normalized_item(db, official_item, llm_client=client)

        assert client.calls == 3
        assert official_run.model_call_count == 1
        assert official_run.outcome == "applied"
        assert {candidate["event_family"] for candidate in official_run.candidate_snapshot} == {
            "gameplay_balance",
            "player_activity",
        }
        assert db.scalar(select(func.count(Event.id))) == 3
        assert db.scalar(select(func.count(EventMention.id))) == 5
        by_family = {
            event.event_family: event for event in db.scalars(select(Event)).all()
        }
        assert by_family["gameplay_balance"].message_count_total == 2
        assert by_family["player_activity"].message_count_total == 2
        assert by_family["cosmetic_release"].message_count_total == 1
        assert by_family["gameplay_balance"].current_summary.startswith("官网确认")
        assert by_family["gameplay_balance"].credibility_level == "officially_confirmed"
        assert by_family["gameplay_balance"].credibility_score == 1
        assert by_family["gameplay_balance"].importance_score == 0.5
        assert by_family["gameplay_balance"].heat_score > 0

        repeated = await aggregate_normalized_item(db, official_item, llm_client=client)
        assert repeated.id == official_run.id
        assert client.calls == 3
        assert db.scalar(select(func.count(EventMention.id))) == 5


@pytest.mark.anyio
async def test_skip_and_update_only_without_candidates_make_zero_calls() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="No-call source")
        db.add(source)
        db.flush()
        skipped = _item(
            db,
            source=source,
            external_id="skipped",
            title="仅媒体",
            message_type="unknown",
            topics=["unknown"],
            entities=[],
            products=["unknown"],
            content_form="media_only",
        )
        repost = _item(
            db,
            source=source,
            external_id="no-candidate-repost",
            title="转载未知事件",
            message_type="game_leak",
            topics=["balance_gameplay"],
            entities=[{"type": "patch", "canonical_id": "patch:99.1"}],
            content_form="repost",
        )
        db.commit()
        client = ScenarioClient()

        skipped_run = await aggregate_normalized_item(db, skipped, llm_client=client)
        repost_run = await aggregate_normalized_item(db, repost, llm_client=client)

        assert client.calls == 0
        assert skipped_run.model_call_count == 0
        assert skipped_run.outcome == "skipped_by_admission"
        assert repost_run.model_call_count == 0
        assert repost_run.outcome == "no_existing_candidate"
        assert db.scalar(select(func.count(EventAggregationRun.id))) == 2


class CollisionClient:
    async def aggregate_events(self, **_payload: object) -> EventAggregationResult:
        return EventAggregationResult.model_validate(
            {
                "mentions": [
                    {
                        "mention_index": 0,
                        "event_family": "gameplay_balance",
                        "action": "create",
                        "candidate_event_id": None,
                        "relation": "reports",
                        "source_role": "known_leaker",
                        "materiality": "material_update",
                        "canonical_anchors": {"patch_version": "patch:collision"},
                        "event_title": "冲突事件",
                        "proposed_summary": "第一项会先进入待提交事务。",
                        "latest_development": "测试事务回滚",
                        "evidence_excerpt": "冲突",
                    },
                    {
                        "mention_index": 1,
                        "event_family": "gameplay_balance",
                        "action": "update",
                        "candidate_event_id": 999999,
                        "relation": "supports",
                        "source_role": "known_leaker",
                        "materiality": "material_update",
                        "canonical_anchors": {"patch_version": "patch:missing"},
                        "proposed_summary": "第二项引用已消失的候选。",
                        "evidence_excerpt": "不存在的候选",
                    },
                ]
            }
        )


class DuplicateIdentityClient:
    async def aggregate_events(self, **payload: object) -> EventAggregationResult:
        message = payload["message"]
        assert isinstance(message, dict)
        title = str(message["title"])
        return EventAggregationResult.model_validate(
            {
                "mentions": [
                    {
                        "mention_index": 0,
                        "event_family": "esports_schedule",
                        "action": "create",
                        "candidate_event_id": None,
                        "relation": "reports",
                        "source_role": "responsible_official",
                        "materiality": "material_update",
                        "canonical_anchors": {
                            "tournament": "2026LPL",
                            "date": "2026-08-01",
                        },
                        "event_title": "8 月 1 日赛程",
                        "proposed_summary": title,
                        "latest_development": title,
                        "key_fact_changes": {
                            "add": [{"fact": title}],
                        },
                        "evidence_excerpt": title,
                    }
                ]
            }
        )


class RepostOfficialRoleClient:
    async def aggregate_events(self, **payload: object) -> EventAggregationResult:
        candidates = payload["candidates"]
        assert isinstance(candidates, list)
        return EventAggregationResult.model_validate(
            {
                "mentions": [
                    {
                        "mention_index": 0,
                        "event_family": "esports_schedule",
                        "action": "update",
                        "candidate_event_id": candidates[0]["event_id"],
                        "relation": "supports",
                        "source_role": "responsible_official",
                        "materiality": "corroboration_only",
                        "canonical_anchors": {"date": "2026-08-01"},
                        "evidence_excerpt": "官方账号转载同日赛程",
                    }
                ]
            }
        )


@pytest.mark.anyio
async def test_official_repost_cannot_claim_responsible_official_role() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        official = Source(name="Reposting official", is_official=True, reliability_score=1)
        db.add(official)
        db.flush()
        original = _item(
            db,
            source=official,
            external_id="original-schedule",
            title="8 月 1 日赛程",
            message_type="esports_announcement",
            topics=["esports_schedule"],
            entities=[],
        )
        repost = _item(
            db,
            source=official,
            external_id="repost-schedule",
            title="转发 8 月 1 日赛程",
            message_type="esports_announcement",
            topics=["esports_schedule"],
            entities=[],
            content_form="repost",
        )
        db.commit()
        event, _created = create_event(
            db,
            normalized_item_id=original.id,
            mention_index=0,
            event_family="esports_schedule",
            products=["lol_pc"],
            canonical_anchors={"date": "2026-08-01"},
            aggregation_key="schedule-2026-08-01",
            title="8 月 1 日赛程",
            current_summary="赛程已公布。",
            source_role="responsible_official",
        )

        await aggregate_normalized_item(db, repost, llm_client=RepostOfficialRoleClient())

        mention = db.scalar(
            select(EventMention).where(
                EventMention.event_id == event.id,
                EventMention.normalized_item_id == repost.id,
            )
        )
        assert mention is not None
        assert mention.source_role == "republisher"


@pytest.mark.anyio
async def test_duplicate_create_identity_updates_existing_event() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        official = Source(
            name="Schedule official", is_official=True, reliability_score=1
        )
        db.add(official)
        db.flush()
        first = _item(
            db,
            source=official,
            external_id="schedule-1",
            title="首次发布赛程",
            message_type="esports_announcement",
            topics=["esports"],
            entities=[],
        )
        second = _item(
            db,
            source=official,
            external_id="schedule-2",
            title="再次发布同日赛程",
            message_type="esports_announcement",
            topics=["esports"],
            entities=[],
        )
        db.commit()
        client = DuplicateIdentityClient()

        await aggregate_normalized_item(db, first, llm_client=client)
        await aggregate_normalized_item(db, second, llm_client=client)

        event = db.scalar(select(Event))
        assert event is not None
        assert event.current_summary == "再次发布同日赛程"
        assert event.message_count_total == 2
        assert len(event.key_facts) == 2
        assert db.scalar(select(func.count(Event.id))) == 1
        assert db.scalar(select(func.count(EventMention.id))) == 2


class FailedModelClient:
    async def aggregate_events(self, **_payload: object) -> EventAggregationResult:
        raise LLMAnalysisError("两次结构校验失败")


@pytest.mark.anyio
async def test_manual_retry_accumulates_model_call_audit() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Retry audit source", reliability_score=0.8)
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id="retry-audit",
            title="26.17 平衡爆料",
            message_type="game_leak",
            topics=["balance_gameplay"],
            entities=[{"type": "patch", "canonical_id": "patch:26.17"}],
        )
        db.commit()

        with pytest.raises(LLMAnalysisError):
            await aggregate_normalized_item(db, item, llm_client=FailedModelClient())
        failed = db.scalar(select(EventAggregationRun))
        assert failed is not None
        assert failed.model_call_count == 2

        completed = await aggregate_normalized_item(db, item, llm_client=ScenarioClient())

        assert completed.status == "completed"
        assert completed.model_call_count == 3


@pytest.mark.anyio
async def test_multi_mention_application_is_atomic() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Atomic source", reliability_score=0.8)
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id="atomic",
            title="一次响应包含冲突动作",
            message_type="game_leak",
            topics=["balance_gameplay"],
            entities=[{"type": "patch", "canonical_id": "patch:collision"}],
        )
        db.commit()

        with pytest.raises(Exception, match="disappeared"):
            await aggregate_normalized_item(db, item, llm_client=CollisionClient())

        assert db.scalar(select(func.count(Event.id))) == 0
        assert db.scalar(select(func.count(EventMention.id))) == 0
        run = db.scalar(select(EventAggregationRun))
        assert run is not None
        assert run.status == "failed"
        assert run.outcome == "apply_error"


class PartialTopicClient:
    async def aggregate_events(self, **_payload: object) -> EventAggregationResult:
        return EventAggregationResult.model_validate(
            {
                "mentions": [
                    {
                        "mention_index": 0,
                        "event_family": "player_activity",
                        "action": "create",
                        "candidate_event_id": None,
                        "relation": "reports",
                        "source_role": "responsible_official",
                        "materiality": "material_update",
                        "canonical_anchors": {"activity_name": "activity:one"},
                        "event_title": "唯一独立活动",
                        "proposed_summary": "只有活动构成独立事件。",
                        "latest_development": "活动公布",
                        "key_fact_changes": {
                            "add": [
                                {"fact": "至臻皮肤 A"},
                                {"fact": "臻彩 B"},
                                {"fact": "图标 C"},
                                {"fact": "边框 D"},
                            ]
                        },
                        "evidence_excerpt": "活动公布",
                    }
                ],
                "ignored_fragments": ["皮肤仅作为活动奖励出现，不构成独立发布事件"],
            }
        )


@pytest.mark.anyio
async def test_multiple_topics_can_produce_only_one_independent_event() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        official = Source(name="Partial topic official", is_official=True, reliability_score=1)
        db.add(official)
        db.flush()
        item = _item(
            db,
            source=official,
            external_id="partial-topics",
            title="活动及作为奖励出现的皮肤",
            message_type="game_announcement",
            topics=["activities_rewards", "cosmetics"],
            entities=[{"type": "activity", "canonical_id": "activity:one"}],
        )
        db.commit()

        run = await aggregate_normalized_item(db, item, llm_client=PartialTopicClient())

        assert run.outcome == "applied"
        assert run.model_call_count == 1
        assert len(run.decision_draft["ignored_fragments"]) == 1
        assert db.scalar(select(func.count(Event.id))) == 1
        assert db.scalar(select(func.count(EventMention.id))) == 1
        event = db.scalar(select(Event))
        assert event is not None
        assert len(event.key_facts) == 4


def test_strong_anchor_conflict_prevents_similar_event_merge() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Anchor conflict source", reliability_score=0.8)
        db.add(source)
        db.flush()
        old_item = _item(
            db,
            source=source,
            external_id="patch-26-17",
            title="版本平衡调整",
            message_type="game_leak",
            topics=["balance_gameplay"],
            entities=[{"type": "patch", "canonical_id": "patch:26.17"}],
        )
        new_item = _item(
            db,
            source=source,
            external_id="patch-26-18",
            title="版本平衡调整",
            message_type="game_leak",
            topics=["balance_gameplay"],
            entities=[{"type": "patch", "canonical_id": "patch:26.18"}],
        )
        db.commit()
        create_event(
            db,
            normalized_item_id=old_item.id,
            mention_index=0,
            event_family="gameplay_balance",
            products=["lol_pc"],
            canonical_anchors={"patch_version": "patch:26.17"},
            aggregation_key="patch-26-17-event",
            title="版本平衡调整",
            current_summary="26.17 平衡调整。",
        )

        candidates = recall_event_candidates(
            db,
            item=new_item,
            family_hints=["gameplay_balance"],
            anchors={"patch_version": "patch:26.18"},
        )

        assert candidates == []
