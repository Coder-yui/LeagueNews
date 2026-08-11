from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.domain.event_admission import decide_event_admission
from app.models.event import Event, EventAggregationRun, EventMention
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.schemas.event_aggregation import EventAggregationResult
from app.services.event_candidates import recall_event_candidates
from app.services.events import create_event
from app.services.llm import LLMAnalysisError
from app.workflows.event_aggregation import _select_content, aggregate_normalized_item


IMPACT = {
    "scope": "product_segment",
    "magnitude": "moderate",
    "duration": "cycle_or_season",
    "urgency": "timely",
}


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
                    "impact": IMPACT,
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
                    "impact": IMPACT,
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
                    "impact": IMPACT,
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
                    "impact": IMPACT,
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
                    "impact": IMPACT,
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
        "impact": IMPACT,
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
        assert by_family["gameplay_balance"].importance_score == 0.56
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
                        "impact": IMPACT,
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
                        "impact": IMPACT,
                        "evidence_excerpt": "不存在的候选",
                    },
                ]
            }
        )


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
                        "impact": IMPACT,
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
