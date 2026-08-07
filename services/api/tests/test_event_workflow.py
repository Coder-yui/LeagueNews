from dataclasses import asdict
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.models.event import Event, EventAggregationRun, EventMessage, EventReviewTask
from app.models.intelligence import Claim
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import KnowledgeRule
from app.domain.ontology import EventRoute
from app.schemas.event_workflow import (
    EventDecisionDraft,
    EventMembershipDraft,
    EventReviewRejection,
    EventReviewCorrectionApproval,
)
from app.api.routes.event_workflows import correct_and_approve_event_review
from app.services.event_aggregation import create_event
from app.services.event_candidates import resolve_aggregation_routes
from app.services.event_decision import (
    stabilize_event_decision,
    validate_event_decision_business,
)
from app.services.llm import LLMClient
from app.workflows.event_aggregation import (
    approve_event_review,
    reject_event_review,
    retry_event_aggregation,
    start_event_aggregation,
)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


def _item(db: Session, source: Source, index: int, title: str) -> NormalizedItem:
    raw = RawItem(
        source_id=source.id,
        external_id=f"workflow-{index}",
        native_title=title,
        content_blocks=[{"id": "b0001", "type": "paragraph", "text": title}],
        published_at=datetime(2026, 6, 16 + index, tzinfo=UTC),
    )
    db.add(raw)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw.id,
        normalized_title=title,
        normalized_text=title,
        summary=f"{title} 摘要",
        entities=[{"name": "26.13", "type": "patch", "role": "core"}],
        primary_topic="patch",
        subtopic="patch_preview",
        source_kind="first_party" if source.is_official else "attributed_report",
        information_stage="preview",
        product_scope="lol_pc",
        importance_score=0.8,
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


def _mock_decisions(
    monkeypatch: pytest.MonkeyPatch,
    decisions: list[EventDecisionDraft],
) -> None:
    async def propose_event(_self: LLMClient, **_kwargs: object) -> EventDecisionDraft:
        return decisions.pop(0)

    monkeypatch.setattr(LLMClient, "propose_event", propose_event)


def test_semantic_candidate_annotation_binds_single_compatible_route() -> None:
    decision = EventDecisionDraft(
        memberships=[
            EventMembershipDraft(
                target="existing:145",
                event_kind="player_activity",
                aggregation_strategy="timeline",
                product_scope="lol_pc",
                aggregation_key="activity:lol_pc:经典战斗之夜",
                identity_resolution="semantic_candidate",
                timeline_note="战斗之夜皮肤现已开放领取",
            )
        ]
    )
    stabilized = stabilize_event_decision(
        decision,
        item={
            "information_stage": "reminder",
            "event_routes": [
                {
                    "event_kind": "player_activity",
                    "aggregation_strategy": "timeline",
                    "product_scope": "lol_pc",
                    "aggregation_key": "activity:lol_pc:战斗之夜",
                    "creation_policy": "existing_only",
                    "assertion": "asserted",
                    "membership_role": "primary",
                }
            ],
        },
        candidates=[
            {
                "event_id": 145,
                "aggregation_key": "activity:lol_pc:经典战斗之夜",
                "event_kind": "player_activity",
                "aggregation_strategy": "timeline",
                "product_scope": "lol_pc",
                "deterministic_route_key": "activity:lol_pc:战斗之夜",
                "reasons": ["发布时间相距 1 天", "实体重叠：战斗之夜"],
            }
        ],
    )

    assert len(stabilized.memberships) == 1
    membership = stabilized.memberships[0]
    assert membership.target == "existing:145"
    assert membership.aggregation_key == "activity:lol_pc:战斗之夜"
    assert membership.identity_resolution == "semantic_candidate"
    assert membership.identity_rationale == "实体重叠：战斗之夜"
    assert membership.evidence_stance == "context"
    assert membership.update_kind == "context"


def test_incompatible_cross_key_candidate_cannot_override_stable_identity() -> None:
    decision = EventDecisionDraft(
        memberships=[
            EventMembershipDraft(
                target="existing:911",
                event_kind="cosmetic_release",
                aggregation_strategy="release",
                product_scope="lol_pc",
                aggregation_key="release:lol_pc:经典皮肤",
                identity_resolution="semantic_candidate",
                identity_rationale="两个对象无关，因此不匹配",
                timeline_note="经典皮肤随经典模式公布",
            )
        ]
    )

    stabilized = stabilize_event_decision(
        decision,
        item={
            "title": "经典模式公布经典皮肤",
            "summary": "经典皮肤随经典模式公布。",
            "information_stage": "announcement",
            "event_routes": [
                {
                    "event_kind": "cosmetic_release",
                    "aggregation_strategy": "release",
                    "product_scope": "lol_pc",
                    "aggregation_key": "release:lol_pc:经典皮肤",
                    "creation_policy": "allow",
                    "assertion": "asserted",
                    "membership_role": "component",
                }
            ],
        },
        candidates=[
            {
                "event_id": 911,
                "aggregation_key": "release:lol_pc:快乐鳃",
                "event_kind": "cosmetic_release",
                "aggregation_strategy": "release",
                "product_scope": "lol_pc",
                "deterministic_route_key": None,
                "match_level": "strong",
                "reasons": ["标题相似度 0.17"],
            }
        ],
    )

    assert len(stabilized.memberships) == 1
    assert stabilized.memberships[0].target == "new"
    assert stabilized.memberships[0].aggregation_key == "release:lol_pc:经典皮肤"
    assert [entry.event_id for entry in stabilized.candidate_rejections] == [911]


def test_repost_without_an_exact_event_does_not_create_one() -> None:
    stabilized = stabilize_event_decision(
        EventDecisionDraft(),
        item={
            "title": "转发玩家同人作品",
            "summary": "分享玩家作品。",
            "information_stage": "announcement",
            "content_form": "repost",
            "event_routes": [
                {
                    "event_kind": "cosmetic_release",
                    "aggregation_strategy": "release",
                    "product_scope": "lol_pc",
                    "aggregation_key": "release:lol_pc:剪纸仙灵",
                    "creation_policy": "existing_only",
                    "assertion": "asserted",
                    "membership_role": "primary",
                }
            ],
        },
        candidates=[],
    )

    assert stabilized.memberships == []


def test_component_scope_is_validated_against_its_route_not_parent_message() -> None:
    route = {
        "event_kind": "gameplay_release",
        "aggregation_strategy": "release",
        "product_scope": "tft",
        "aggregation_key": "release:tft:恭喜发财",
        "creation_policy": "allow",
        "assertion": "asserted",
        "membership_role": "component",
    }
    candidate = {
        "event_id": 921,
        "aggregation_key": "release:tft:恭喜发财",
        "event_kind": "gameplay_release",
        "aggregation_strategy": "release",
        "product_scope": "tft",
    }
    decision = EventDecisionDraft(
        memberships=[
            EventMembershipDraft(
                target="existing:921",
                event_kind="gameplay_release",
                aggregation_strategy="release",
                product_scope="tft",
                aggregation_key="release:tft:恭喜发财",
                identity_resolution="exact_key",
                membership_role="component",
                timeline_note="恭喜发财模式回归",
            )
        ]
    )

    error = validate_event_decision_business(
        decision,
        item={"product_scope": "lol_pc", "event_routes": [route]},
        candidates=[candidate],
        allowed_new_keys={"release:tft:恭喜发财"},
    )

    assert error is None


def test_resolved_hotfix_route_binds_without_model_selection() -> None:
    candidates = [
        {
            "event_id": 146,
            "aggregation_key": "hotfix:lol_pc:2026-08-05",
            "event_kind": "gameplay_update",
            "aggregation_strategy": "timeline",
            "product_scope": "lol_pc",
            "deterministic_route_key": "hotfix:lol_pc:2026-08-06",
            "reasons": ["短窗口热更新连续：核心对象重叠：佛耶戈"],
        }
    ]
    routes = resolve_aggregation_routes(
        [
            EventRoute(
                event_kind="gameplay_update",
                aggregation_strategy="timeline",
                product_scope="lol_pc",
                aggregation_key="hotfix:lol_pc:2026-08-06",
            )
        ],
        candidates,
    )
    stabilized = stabilize_event_decision(
        EventDecisionDraft(),
        item={
            "title": "8月6日不停机更新公告",
            "summary": "佛耶戈等问题已修复。",
            "information_stage": "active",
            "event_routes": [asdict(route) for route in routes],
        },
        candidates=candidates,
    )

    assert len(stabilized.memberships) == 1
    membership = stabilized.memberships[0]
    assert membership.target == "existing:146"
    assert membership.aggregation_key == "hotfix:lol_pc:2026-08-05"
    assert membership.identity_resolution == "exact_key"
    assert membership.identity_rationale == "稳定聚合键精确匹配"
    assert membership.update_kind == "confirmation"


@pytest.mark.anyio
async def test_create_draft_does_not_write_event_until_approved(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source(name="Workflow Create", connector_type="manual")
    db.add(source)
    db.commit()
    item = _item(db, source, 0, "26.13 版本预览")
    _mock_decisions(
        monkeypatch,
        [
            EventDecisionDraft(
                memberships=[
                    EventMembershipDraft(
                        target="new",
                        event_kind="gameplay_update",
                        aggregation_strategy="patch_cycle",
                        product_scope="lol_pc",
                        aggregation_key="patch:lol_pc:26.13",
                        timeline_note="版本预览发布",
                        lifecycle_status="developing",
                    )
                ]
            )
        ],
    )

    run = await start_event_aggregation(db, item)

    assert run.status == "awaiting_review"
    assert db.scalar(select(func.count(Event.id))) == 0
    review = db.scalar(
        select(EventReviewTask).where(
            EventReviewTask.event_aggregation_run_id == run.id
        )
    )
    approve_event_review(db, review, note="确认")

    event = db.scalar(select(Event))
    assert event.aggregation_key == "patch:lol_pc:26.13"
    assert event.event_kind == "gameplay_update"
    assert event.aggregation_strategy == "patch_cycle"
    assert event.current_revision == 1
    assert run.status == "completed"
    assert run.outcome == "created"


@pytest.mark.anyio
async def test_update_can_only_apply_snapshotted_candidate(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source(name="Workflow Update", connector_type="manual")
    db.add(source)
    db.commit()
    preview = _item(db, source, 0, "26.13 版本预览")
    event = create_event(
        db,
        normalized_item_id=preview.id,
        aggregation_key="patch:lol_pc:26.13",
        title="26.13 版本预览",
        summary="初始",
        event_kind="gameplay_update",
        aggregation_strategy="patch_cycle",
        product_scope="lol_pc",
    )
    full = _item(db, source, 1, "26.13 版本完整预览")
    _mock_decisions(
        monkeypatch,
        [
            EventDecisionDraft(
                memberships=[
                    EventMembershipDraft(
                        target=f"existing:{event.id}",
                        event_kind="gameplay_update",
                        aggregation_strategy="patch_cycle",
                        product_scope="lol_pc",
                        aggregation_key="patch:lol_pc:26.13",
                        timeline_note="公布完整数值",
                        lifecycle_status="developing",
                    )
                ]
            )
        ],
    )
    run = await start_event_aggregation(db, full)
    review = db.scalar(
        select(EventReviewTask).where(
            EventReviewTask.event_aggregation_run_id == run.id
        )
    )
    approve_event_review(db, review, note=None)

    assert db.get(Event, event.id).current_revision == 2
    assert db.scalar(
        select(func.count(EventMessage.event_id)).where(
            EventMessage.event_id == event.id
        )
    ) == 2
    assert run.outcome == "updated"


@pytest.mark.anyio
async def test_not_event_approval_leaves_formal_events_unchanged(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source(name="Workflow Not Event", connector_type="manual")
    db.add(source)
    db.commit()
    item = _item(db, source, 0, "一次性公告")
    _mock_decisions(
        monkeypatch,
        [EventDecisionDraft(memberships=[])],
    )
    run = await start_event_aggregation(db, item)
    review = db.scalar(
        select(EventReviewTask).where(
            EventReviewTask.event_aggregation_run_id == run.id
        )
    )
    approve_event_review(db, review, note=None)

    assert run.outcome == "not_event"
    assert db.scalar(select(func.count(Event.id))) == 0


@pytest.mark.anyio
async def test_correct_and_approve_executes_validated_human_membership_changes(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source(name="Human correction", reliability_score=0.7)
    db.add(source)
    db.commit()
    first = _item(db, source, 10, "26.13 版本预览")
    event = create_event(db, normalized_item_id=first.id, aggregation_key="patch:lol_pc:26.13", title="26.13", summary="预览", event_kind="gameplay_update", aggregation_strategy="patch_cycle", product_scope="lol_pc")
    update = _item(db, source, 11, "26.13 数值争议")
    _mock_decisions(monkeypatch, [EventDecisionDraft(memberships=[])])
    run = await start_event_aggregation(db, update)
    review = db.scalar(select(EventReviewTask).where(EventReviewTask.event_aggregation_run_id == run.id))
    corrected = EventMembershipDraft(target=f"existing:{event.id}", event_kind="gameplay_update", aggregation_strategy="patch_cycle", product_scope="lol_pc", aggregation_key="patch:lol_pc:26.13", membership_role="component", evidence_stance="contradicts", update_kind="correction", timeline_note="人工修正为反对证据")
    correct_and_approve_event_review(review.id, EventReviewCorrectionApproval(decision_draft={"memberships": [corrected.model_dump(mode="json")], "candidate_rejections": []}), db)
    membership = db.get(EventMessage, (event.id, update.id))
    assert run.decision_draft["memberships"][0]["target"] == f"existing:{event.id}"
    assert review.proposal["decision"]["memberships"][0]["membership_role"] == "component"
    assert membership.membership_role == "component"
    assert membership.evidence_stance == "contradicts"
    assert membership.timeline_note == "人工修正为反对证据"


@pytest.mark.anyio
async def test_one_message_can_create_primary_and_component_memberships(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source(name="Workflow Multi", connector_type="riot_official")
    db.add(source)
    db.commit()
    item = _item(db, source, 0, "26.13 更新公告：经典模式上线")
    item.entities = [
        {"name": "26.13", "type": "patch", "role": "core"},
        {"name": "经典模式", "type": "game_mode", "role": "affected"},
    ]
    db.add(
        Claim(
            normalized_item_id=item.id,
            subject={"name": "26.13", "type": "patch"},
            predicate="adds_mode",
            object_value={
                "mode": {"name": "经典模式", "type": "game_mode"}
            },
            temporal_role="event",
            extraction_model="test",
        )
    )
    db.commit()
    _mock_decisions(
        monkeypatch,
        [
            EventDecisionDraft(
                memberships=[
                    EventMembershipDraft(
                        target="new",
                        event_kind="gameplay_update",
                        aggregation_strategy="patch_cycle",
                        product_scope="lol_pc",
                        aggregation_key="patch:lol_pc:26.13",
                        membership_role="primary",
                        timeline_note="26.13 更新公告发布",
                    ),
                    EventMembershipDraft(
                        target="new",
                        event_kind="gameplay_release",
                        aggregation_strategy="release",
                        product_scope="lol_pc",
                        aggregation_key="gameplay:lol_pc:经典模式",
                        membership_role="component",
                        timeline_note="经典模式随版本更新上线",
                    ),
                ]
            )
        ],
    )

    run = await start_event_aggregation(db, item)
    review = db.scalar(
        select(EventReviewTask).where(
            EventReviewTask.event_aggregation_run_id == run.id
        )
    )
    approve_event_review(db, review, note=None)

    messages = list(
        db.scalars(
            select(EventMessage)
            .where(EventMessage.normalized_item_id == item.id)
            .order_by(EventMessage.membership_role)
        )
    )
    assert {message.membership_role for message in messages} == {
        "primary",
        "component",
    }
    assert {
        db.get(Event, message.event_id).event_kind for message in messages
    } == {"gameplay_update", "gameplay_release"}
    assert run.outcome == "created"


@pytest.mark.anyio
async def test_negated_release_does_not_create_event(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source(name="Concept", connector_type="manual")
    db.add(source)
    db.commit()
    item = _item(db, source, 10, "概念皮肤不会上线")
    item.primary_topic = "skin"
    item.subtopic = "skin_release"
    item.entities = [
        {"name": "概念皮肤", "type": "skin", "role": "core"}
    ]
    item.facets = {"event_assertion": "negated"}
    db.commit()
    _mock_decisions(monkeypatch, [EventDecisionDraft(memberships=[])])

    run = await start_event_aggregation(db, item)
    review = db.scalar(
        select(EventReviewTask).where(
            EventReviewTask.event_aggregation_run_id == run.id
        )
    )
    approve_event_review(db, review, note=None)

    assert run.outcome == "not_event"
    assert db.scalar(select(func.count(Event.id))) == 0


@pytest.mark.anyio
async def test_event_approval_recovers_when_same_key_appears_after_review(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source(name="Concurrent", connector_type="manual")
    db.add(source)
    db.commit()
    update = _item(db, source, 10, "26.13 版本补充")
    _mock_decisions(
        monkeypatch,
        [
            EventDecisionDraft(
                memberships=[
                    EventMembershipDraft(
                        target="new",
                        event_kind="gameplay_update",
                        aggregation_strategy="patch_cycle",
                        product_scope="lol_pc",
                        aggregation_key="patch:lol_pc:26.13",
                        timeline_note="26.13 版本补充",
                    )
                ]
            )
        ],
    )
    run = await start_event_aggregation(db, update)
    review = db.scalar(
        select(EventReviewTask).where(
            EventReviewTask.event_aggregation_run_id == run.id
        )
    )
    first = _item(db, source, 11, "26.13 版本预览")
    event = create_event(
        db,
        normalized_item_id=first.id,
        aggregation_key="patch:lol_pc:26.13",
        title="26.13 版本",
        summary="版本预览",
        event_kind="gameplay_update",
        aggregation_strategy="patch_cycle",
        product_scope="lol_pc",
    )

    approve_event_review(db, review, note=None)

    assert db.get(EventMessage, (event.id, update.id)) is not None
    assert db.scalar(select(func.count(Event.id))) == 1
    assert run.outcome == "updated"


@pytest.mark.anyio
async def test_reject_records_knowledge_and_retry_supersedes_run(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source(name="Workflow Retry", connector_type="manual")
    db.add(source)
    db.commit()
    item = _item(db, source, 0, "26.13 版本预览")
    _mock_decisions(
        monkeypatch,
        [
            EventDecisionDraft(memberships=[]),
            EventDecisionDraft(
                memberships=[
                    EventMembershipDraft(
                        target="new",
                        event_kind="gameplay_update",
                        aggregation_strategy="patch_cycle",
                        product_scope="lol_pc",
                        aggregation_key="patch:lol_pc:26.13",
                        timeline_note="纠正后创建版本时间线",
                    )
                ]
            ),
        ],
    )
    first = await start_event_aggregation(db, item)
    review = db.scalar(
        select(EventReviewTask).where(
            EventReviewTask.event_aggregation_run_id == first.id
        )
    )
    reject_event_review(
        db,
        review,
        payload=EventReviewRejection(
            reason="版本预览应进入事件层",
            knowledge_rule="正式版本预览应进入对应 patch 事件。",
        ),
    )

    assert db.scalar(select(func.count(Event.id))) == 0
    rule = db.scalar(
        select(KnowledgeRule).where(
            KnowledgeRule.knowledge_type == "event_aggregation"
        )
    )
    assert rule.source_event_review_id == review.id

    retried = await retry_event_aggregation(db, first)
    assert retried.supersedes_run_id == first.id
    assert retried.status == "awaiting_review"
    assert db.scalar(select(func.count(EventAggregationRun.id))) == 2


@pytest.mark.anyio
async def test_superseded_item_cannot_start_event_aggregation(
    db: Session,
) -> None:
    source = Source(name="Superseded Workflow", connector_type="riot_official")
    db.add(source)
    db.commit()
    old = _item(db, source, 0, "旧版活动页面")
    successor_raw = RawItem(
        source_id=source.id,
        external_id="workflow-successor",
        native_title="新版活动页面",
        content_blocks=[
            {"id": "b0001", "type": "paragraph", "text": "新版活动页面"}
        ],
        published_at=datetime(2026, 6, 20, tzinfo=UTC),
        revision=2,
        supersedes_raw_item_id=old.raw_item_id,
    )
    db.add(successor_raw)
    db.commit()

    with pytest.raises(ValueError, match="superseded"):
        await start_event_aggregation(db, old)
