from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.models.event import Event, EventAggregationRun, EventMessage, EventReviewTask
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import KnowledgeRule
from app.schemas.event_workflow import (
    EventDecisionDraft,
    EventMembershipDraft,
    EventReviewRejection,
)
from app.services.event_aggregation import create_event
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
        category="版本更新",
        entities=[{"name": "26.13", "type": "patch"}],
        importance_score=0.8,
        credibility="official",
        credibility_score=1,
        credibility_evidence=[],
        target_language="zh-CN",
        translated_title=title,
        translated_content_blocks=[],
        translation_status="not_required",
        analysis_model="test",
        analysis_version="test",
        content_type="official_notice",
        primary_topic="patch",
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
                        event_type="patch_cycle",
                        aggregation_key="patch:26.13",
                        timeline_note="版本预览发布",
                        lifecycle_status="developing",
                        is_official_confirmation=True,
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
    assert event.aggregation_key == "patch:26.13"
    assert event.event_type == "patch_cycle"
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
        event_key="patch:26.13",
        aggregation_key="patch:26.13",
        title="26.13 版本预览",
        summary="初始",
        category="版本更新",
    )
    full = _item(db, source, 1, "26.13 版本完整预览")
    _mock_decisions(
        monkeypatch,
        [
            EventDecisionDraft(
                memberships=[
                    EventMembershipDraft(
                        target=f"existing:{event.id}",
                        event_type="patch_cycle",
                        aggregation_key="patch:26.13",
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
async def test_one_message_can_create_primary_and_component_memberships(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source(name="Workflow Multi", connector_type="riot_official")
    db.add(source)
    db.commit()
    item = _item(db, source, 0, "26.13 更新公告：经典模式上线")
    _mock_decisions(
        monkeypatch,
        [
            EventDecisionDraft(
                memberships=[
                    EventMembershipDraft(
                        target="new",
                        event_type="patch_cycle",
                        aggregation_key="patch:26.13",
                        membership_role="primary",
                        timeline_note="26.13 更新公告发布",
                        is_official_confirmation=True,
                    ),
                    EventMembershipDraft(
                        target="new",
                        event_type="major_gameplay_change",
                        aggregation_key="gameplay:经典模式",
                        membership_role="component",
                        timeline_note="经典模式随版本更新上线",
                        is_official_confirmation=True,
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
        db.get(Event, message.event_id).event_type for message in messages
    } == {"patch_cycle", "major_gameplay_change"}
    assert run.outcome == "created"


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
                        event_type="patch_cycle",
                        aggregation_key="patch:26.13",
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
