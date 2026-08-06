from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base, get_db
from app.main import app
from app.models.credibility import SourceReliabilityHistory
from app.models.event import EventMessage, EventRevision
from app.models.intelligence import EventClaim
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.services.event_aggregation import (
    add_message_to_event,
    create_event,
    expire_stale_unconfirmed_events,
)
from app.services.claims import extract_traceable_claim


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _add_normalized_item(
    db: Session,
    *,
    source: Source,
    external_id: str,
    title: str,
    published_at: datetime | None,
    credibility: str = "official",
    credibility_score: float = 1.0,
    revision: int = 1,
    supersedes_raw_item_id: int | None = None,
) -> NormalizedItem:
    raw_item = RawItem(
        source_id=source.id,
        external_id=external_id,
        native_title=title,
        canonical_url=f"https://example.com/{external_id}",
        language="en",
        content_blocks=[{"id": "b0001", "type": "paragraph", "text": title}],
        published_at=published_at,
        revision=revision,
        supersedes_raw_item_id=supersedes_raw_item_id,
    )
    db.add(raw_item)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw_item.id,
        normalized_title=title,
        normalized_text=title,
        summary=f"{title} summary",
        category="版本更新",
        entities=[{"name": "26.13", "type": "patch"}],
        importance_score=0.9,
        credibility=credibility,
        credibility_score=credibility_score,
        credibility_evidence=["官方设计师"],
        language="zh-CN",
        source_language="en",
        target_language="zh-CN",
        translated_title=title,
        translated_text=title,
        translated_content_blocks=[{"id": "b0001", "type": "paragraph", "text": title}],
        translation_status="translated",
        translation_model="test",
        analysis_model="test",
        analysis_version="test",
    )
    db.add(item)
    db.commit()
    return item


def test_create_and_update_event_tracks_membership_time_and_revisions() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="RiotPhroxzon", connector_type="x_twitter")
        db.add(source)
        db.commit()
        preview = _add_normalized_item(
            db,
            source=source,
            external_id="preview",
            title="26.13 Preview",
            published_at=datetime(2026, 6, 16, tzinfo=UTC),
        )
        full_preview = _add_normalized_item(
            db,
            source=source,
            external_id="full-preview",
            title="26.13 Full Preview",
            published_at=datetime(2026, 6, 17, tzinfo=UTC),
        )

        event = create_event(
            db,
            normalized_item_id=preview.id,
            event_key="patch:26.13",
            title="英雄联盟 26.13 版本预览",
            summary="设计师发布了 26.13 版本预览。",
            category="版本更新",
            evidence={"match": "patch_key"},
        )
        event, added = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=full_preview.id,
            title="英雄联盟 26.13 版本完整预览",
            summary="设计师补充了 26.13 版本的完整改动。",
            evidence={"match": "patch:26.13"},
        )

        assert added is True
        assert event.current_revision == 2
        assert event.first_published_at == datetime(2026, 6, 16)
        assert event.last_published_at == datetime(2026, 6, 17)
        assert db.scalar(
            select(EventMessage).where(
                EventMessage.normalized_item_id == preview.id
            )
        )
        assert len(
            list(
                db.scalars(
                    select(EventRevision)
                    .where(EventRevision.event_id == event.id)
                    .order_by(EventRevision.revision)
                )
            )
        ) == 2


def test_repeating_same_message_is_idempotent() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Riot Designer", connector_type="x_twitter")
        db.add(source)
        db.commit()
        item = _add_normalized_item(
            db,
            source=source,
            external_id="preview",
            title="26.13 Preview",
            published_at=datetime(2026, 6, 16, tzinfo=UTC),
        )
        event = create_event(
            db,
            normalized_item_id=item.id,
            event_key="patch:26.13",
            title="26.13 版本预览",
            summary="初始摘要",
            category="版本更新",
        )

        repeated, added = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=item.id,
            title="不应应用的重复标题",
        )

        assert added is False
        assert repeated.title == "26.13 版本预览"
        assert repeated.current_revision == 1
        assert db.scalar(
            select(func.count(EventMessage.event_id)).where(
                EventMessage.event_id == event.id
            )
        ) == 1
        assert db.scalar(
            select(func.count(EventRevision.id)).where(
                EventRevision.event_id == event.id
            )
        ) == 1


def test_new_raw_revision_replaces_membership_and_event_claim_atomically() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Versioned source", connector_type="manual")
        db.add(source)
        db.commit()
        previous = _add_normalized_item(
            db,
            source=source,
            external_id="version-1",
            title="Version one",
            published_at=datetime(2026, 6, 16, tzinfo=UTC),
        )
        previous_claim = extract_traceable_claim(db, previous)
        db.commit()
        event = create_event(
            db,
            normalized_item_id=previous.id,
            title="Versioned event",
            summary="Initial",
            category="测试",
        )
        replacement = _add_normalized_item(
            db,
            source=source,
            external_id="version-2",
            title="Version two",
            published_at=datetime(2026, 6, 17, tzinfo=UTC),
            revision=2,
            supersedes_raw_item_id=previous.raw_item_id,
        )
        replacement_claim = extract_traceable_claim(db, replacement)
        db.commit()

        _, added = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=replacement.id,
            summary="Replacement",
        )

        assert added is True
        assert db.get(EventClaim, (event.id, previous_claim.id)) is None
        assert db.get(EventClaim, (event.id, replacement_claim.id)) is not None
        assert db.scalar(
            select(EventMessage).where(
                EventMessage.normalized_item_id == previous.id
            )
        ) is None
        assert db.scalar(
            select(EventMessage).where(
                EventMessage.normalized_item_id == replacement.id,
                EventMessage.membership_status == "active",
            )
        ) is not None


def test_message_can_belong_to_two_events_with_distinct_roles() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Source", connector_type="manual")
        db.add(source)
        db.commit()
        first = _add_normalized_item(
            db,
            source=source,
            external_id="first",
            title="First",
            published_at=datetime(2026, 6, 16, tzinfo=UTC),
        )
        second = _add_normalized_item(
            db,
            source=source,
            external_id="second",
            title="Second",
            published_at=datetime(2026, 6, 17, tzinfo=UTC),
        )
        create_event(
            db,
            normalized_item_id=first.id,
            title="Event One",
            summary="One",
            category="测试",
        )
        event_two = create_event(
            db,
            normalized_item_id=second.id,
            title="Event Two",
            summary="Two",
            category="测试",
        )

        _, added = add_message_to_event(
            db,
            event_id=event_two.id,
            normalized_item_id=first.id,
            membership_role="component",
        )
        assert added is True
        memberships = list(
            db.scalars(
                select(EventMessage)
                .where(EventMessage.normalized_item_id == first.id)
                .order_by(EventMessage.event_id)
            )
        )
        assert len(memberships) == 2
        assert [membership.membership_role for membership in memberships] == [
            "primary",
            "component",
        ]

        _, added = add_message_to_event(
            db,
            event_id=event_two.id,
            normalized_item_id=first.id,
            membership_role="cross_ref",
        )
        assert added is False


def test_event_read_api_returns_timeline_and_revision_history() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="API Source", connector_type="manual")
        db.add(source)
        db.commit()
        item = _add_normalized_item(
            db,
            source=source,
            external_id="api-item",
            title="API Item",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
        event = create_event(
            db,
            normalized_item_id=item.id,
            event_key="test:api",
            title="API Event",
            summary="API summary",
            category="测试",
        )
        newer_item = _add_normalized_item(
            db,
            source=source,
            external_id="api-item-newer",
            title="API Item Newer",
            published_at=datetime(2026, 7, 2, tzinfo=UTC),
        )
        event, added = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=newer_item.id,
            summary="API summary updated",
        )
        assert added is True
        undated_item = _add_normalized_item(
            db,
            source=source,
            external_id="api-item-undated",
            title="API Item Without Publish Time",
            published_at=None,
        )
        event, added = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=undated_item.id,
        )
        assert added is True
        event_id = event.id
        newer_item_id = newer_item.id
        undated_item_id = undated_item.id

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            listing = client.get("/api/v1/events")
            filtered = client.get(
                "/api/v1/events?event_type=other&lifecycle_status=confirmed&limit=1&offset=0"
            )
            event_page = client.get(f"/api/v1/events/page?search={event_id}")
            message_page = client.get(
                f"/api/v1/normalized-items/published-page?search={newer_item_id}"
            )
            detail = client.get(f"/api/v1/events/{event_id}")
            messages = client.get(f"/api/v1/events/{event_id}/messages")
            updated = client.patch(
                f"/api/v1/events/{event_id}",
                json={
                    "lifecycle_status": "confirmed",
                    "summary": "Admin-updated summary",
                    "change_note": "API test update",
                },
            )
            withdrawn = client.delete(
                f"/api/v1/events/{event_id}/messages/{undated_item_id}"
            )
            relinked = client.post(
                f"/api/v1/events/{event_id}/messages/{undated_item_id}",
                json={
                    "membership_role": "component",
                    "evidence_stance": "context",
                    "timeline_note": "API test relink",
                },
            )
            missing = client.get("/api/v1/events/9999")
    finally:
        app.dependency_overrides.clear()

    assert listing.status_code == 200
    assert listing.json()[0]["event_key"] == "test:api"
    assert listing.json()[0]["message_count"] == 3
    assert filtered.status_code == 200
    assert event_page.status_code == 200
    assert event_page.json()["total"] == 1
    assert event_page.json()["items"][0]["id"] == event_id
    assert message_page.status_code == 200
    assert message_page.json()["total"] == 1
    assert message_page.json()["items"][0]["id"] == newer_item_id
    assert [row["event_key"] for row in filtered.json()] == ["test:api"]
    assert detail.status_code == 200
    assert detail.json()["revisions"][0]["revision"] == 1
    assert [message["title"] for message in detail.json()["messages"]] == [
        "API Item Newer",
        "API Item",
        "API Item Without Publish Time",
    ]
    assert messages.status_code == 200
    assert [message["title"] for message in messages.json()] == [
        "API Item Newer",
        "API Item",
        "API Item Without Publish Time",
    ]
    assert updated.status_code == 200
    assert updated.json()["lifecycle_status"] == "confirmed"
    assert updated.json()["summary"] == "Admin-updated summary"
    assert updated.json()["revisions"][-1]["change_note"] == "API test update"
    assert withdrawn.status_code == 200
    assert withdrawn.json()["message_count"] == 2
    assert withdrawn.json()["revisions"][-1]["change_note"] == (
        f"管理台解除消息 {undated_item_id} 关联"
    )
    assert relinked.status_code == 200
    assert relinked.json()["message_count"] == 3
    relinked_message = next(
        message
        for message in relinked.json()["messages"]
        if message["normalized_item_id"] == undated_item_id
    )
    assert relinked_message["membership_role"] == "component"
    assert relinked_message["evidence_stance"] == "context"
    assert missing.status_code == 404
    assert missing.json()["detail"] == "event not found"


def test_event_membership_uses_restrict_delete_policy() -> None:
    foreign_key = next(iter(EventMessage.__table__.c.normalized_item_id.foreign_keys))
    assert foreign_key.ondelete == "RESTRICT"


def test_event_credibility_counts_independent_sources_not_message_volume() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        first_source = Source(name="Reporter One", connector_type="x_twitter")
        second_source = Source(name="Reporter Two", connector_type="weibo")
        db.add_all([first_source, second_source])
        db.commit()
        first = _add_normalized_item(
            db,
            source=first_source,
            external_id="rumor-1",
            title="传闻：选手加入战队",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
            credibility="unverified",
            credibility_score=0.6,
        )
        same_source = _add_normalized_item(
            db,
            source=first_source,
            external_id="rumor-2",
            title="同一记者补充转会消息",
            published_at=datetime(2026, 7, 2, tzinfo=UTC),
            credibility="unverified",
            credibility_score=0.6,
        )
        independent = _add_normalized_item(
            db,
            source=second_source,
            external_id="rumor-3",
            title="第二名记者独立印证",
            published_at=datetime(2026, 7, 3, tzinfo=UTC),
            credibility="unverified",
            credibility_score=0.6,
        )

        event = create_event(
            db,
            normalized_item_id=first.id,
            title="传闻：选手加入战队",
            summary="单源转会爆料。",
            category="转会",
            event_type="transfer",
            lifecycle_status="unconfirmed",
            is_official_confirmation=False,
        )
        assert event.credibility_status == "single_source"
        assert event.credibility_score == pytest.approx(0.6)

        event, _ = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=same_source.id,
            is_official_confirmation=False,
            is_significant_update=False,
        )
        assert event.independent_source_count == 1
        assert event.credibility_score == pytest.approx(0.6)
        assert event.current_revision == 1

        event, _ = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=independent.id,
            is_official_confirmation=False,
            is_significant_update=False,
        )
        assert event.independent_source_count == 2
        assert event.credibility_status == "multi_source_confirmed"
        assert event.credibility_score == pytest.approx(0.84)
        assert event.current_revision == 1


def test_event_credibility_uses_full_item_strength_without_legacy_cap() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Reliable insider", connector_type="x_twitter")
        db.add(source)
        db.commit()
        item = _add_normalized_item(
            db,
            source=source,
            external_id="high-confidence",
            title="高确定性爆料",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
            credibility="unverified",
            credibility_score=0.95,
        )

        event = create_event(
            db,
            normalized_item_id=item.id,
            title="传闻事件",
            summary="单一高确定性信源。",
            category="转会",
            event_type="transfer",
            lifecycle_status="unconfirmed",
            is_official_confirmation=False,
        )

        assert event.credibility_score == pytest.approx(0.95)


def test_official_confirmation_overrides_event_credibility() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        reporter = Source(name="Reporter", connector_type="x_twitter")
        team = Source(name="Team Official", connector_type="weibo")
        db.add_all([reporter, team])
        db.commit()
        rumor = _add_normalized_item(
            db,
            source=reporter,
            external_id="rumor",
            title="传闻：选手加入战队",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
            credibility="unverified",
            credibility_score=0.5,
        )
        announcement = _add_normalized_item(
            db,
            source=team,
            external_id="official",
            title="战队官宣选手加入",
            published_at=datetime(2026, 7, 3, tzinfo=UTC),
        )
        event = create_event(
            db,
            normalized_item_id=rumor.id,
            title="传闻：选手加入战队",
            summary="尚未确认。",
            category="转会",
            event_type="transfer",
            lifecycle_status="unconfirmed",
            is_official_confirmation=False,
        )
        event, _ = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=announcement.id,
            title="战队官宣选手加入",
            lifecycle_status="confirmed",
            is_official_confirmation=True,
            change_note="战队正式官宣",
        )

        assert event.credibility_status == "official_confirmed"
        assert event.credibility_score == 1
        assert event.lifecycle_status == "confirmed"
        assert event.official_source_count == 1
        history = db.scalar(
            select(SourceReliabilityHistory).where(
                SourceReliabilityHistory.source_id == reporter.id
            )
        )
        assert history is not None
        assert history.confirmed_count == 1
        assert history.refuted_count == 0


def test_late_schedule_cannot_regress_completed_match_event() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="LPL Official", connector_type="weibo")
        db.add(source)
        db.commit()
        result = _add_normalized_item(
            db,
            source=source,
            external_id="result",
            title="7月26日赛果",
            published_at=datetime(2026, 7, 26, 14, 31, tzinfo=UTC),
        )
        schedule = _add_normalized_item(
            db,
            source=source,
            external_id="schedule",
            title="7月26日赛程预告",
            published_at=datetime(2026, 7, 26, 6, 11, tzinfo=UTC),
        )
        event = create_event(
            db,
            normalized_item_id=result.id,
            event_key="matchday:lpl:2026-07-26",
            title="7月26日赛果",
            summary="三场比赛已经结束。",
            category="LPL赛程赛果",
            event_type="match",
            lifecycle_status="completed",
        )

        event, added = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=schedule.id,
            title="7月26日赛程预告",
            summary="三场比赛即将开始。",
            lifecycle_status="scheduled",
            is_significant_update=True,
        )

        assert added is True
        assert event.lifecycle_status == "completed"
        assert event.title == "7月26日赛果"
        assert event.summary == "三场比赛已经结束。"
        assert event.current_revision == 1
        assert len(event.messages) == 2


def test_new_raw_revision_replaces_event_member_without_duplicate_revision() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Versioned Official", connector_type="riot_official")
        db.add(source)
        db.commit()
        old = _add_normalized_item(
            db,
            source=source,
            external_id="article",
            title="活动开放申请",
            published_at=datetime(2026, 7, 22, tzinfo=UTC),
        )
        event = create_event(
            db,
            normalized_item_id=old.id,
            title="活动开放申请",
            summary="活动已开放申请。",
            category="社区活动",
        )
        new = _add_normalized_item(
            db,
            source=source,
            external_id="article",
            title="活动开放申请（页面修订）",
            published_at=datetime(2026, 7, 22, tzinfo=UTC),
            revision=2,
            supersedes_raw_item_id=old.raw_item_id,
        )

        event, added = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=new.id,
            is_significant_update=False,
        )

        assert added is True
        assert event.current_revision == 1
        assert list(
            db.scalars(
                select(EventMessage.normalized_item_id).where(
                    EventMessage.event_id == event.id
                )
            )
        ) == [new.id]
        assert db.scalar(
            select(func.count(EventRevision.id)).where(
                EventRevision.event_id == event.id
            )
        ) == 1


def test_event_importance_uses_member_base_and_event_type_cap() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="CN Rotation", connector_type="baidu_tieba")
        db.add(source)
        db.commit()
        weekly = _add_normalized_item(
            db,
            source=source,
            external_id="weekly",
            title="神话商城每周轮换",
            published_at=datetime(2026, 7, 23, tzinfo=UTC),
        )
        daily = _add_normalized_item(
            db,
            source=source,
            external_id="daily",
            title="神话商城每日轮换",
            published_at=datetime(2026, 7, 24, tzinfo=UTC),
        )
        event = create_event(
            db,
            normalized_item_id=weekly.id,
            title="2026年第30周国服神话商城轮换",
            summary="本周轮换。",
            category="国服活动",
            event_type="activity",
            importance_score=0.4,
        )
        event, _ = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=daily.id,
            is_significant_update=False,
            importance_score=0.35,
        )

        assert weekly.importance_score == 0.9
        assert daily.importance_score == 0.9
        assert event.importance_score == 0.75
        assert event.current_revision == 1


def test_unconfirmed_timeline_expires_and_decays_idempotently() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Rumor Source", connector_type="weibo")
        db.add(source)
        db.commit()
        rumor = _add_normalized_item(
            db,
            source=source,
            external_id="stale-rumor",
            title="传闻：WBG 正在考虑新的打野候选",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
            credibility="unverified",
            credibility_score=0.6,
        )
        event = create_event(
            db,
            normalized_item_id=rumor.id,
            aggregation_key="WBG:jungle:2026off",
            title="WBG 打野转会",
            summary="WBG 正在考察打野候选。",
            category="转会",
            event_type="transfer_saga",
            lifecycle_status="unconfirmed",
            is_official_confirmation=False,
        )

        assert expire_stale_unconfirmed_events(
            db,
            as_of=datetime(2026, 7, 10, tzinfo=UTC),
        ) == []
        affected = expire_stale_unconfirmed_events(
            db,
            as_of=datetime(2026, 7, 29, tzinfo=UTC),
        )
        assert affected == [event.id]
        assert event.lifecycle_status == "expired_unconfirmed"
        assert event.credibility_status == "expired_unconfirmed"
        assert event.credibility_score == pytest.approx(0.3)
        assert event.current_revision == 2
        assert event.revisions[-1].evidence_snapshot["decay_factor"] == 0.5

        repeated = expire_stale_unconfirmed_events(
            db,
            as_of=datetime(2026, 7, 29, tzinfo=UTC),
        )
        assert repeated == [event.id]
        assert event.credibility_score == pytest.approx(0.3)
        assert event.current_revision == 2
