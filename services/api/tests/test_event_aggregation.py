from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base, get_db
from app.main import app
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
    revision: int = 1,
    supersedes_raw_item_id: int | None = None,
    quoted_url: str | None = None,
    primary_topic: str = "patch",
    subtopic: str = "patch_preview",
    product_scope: str = "lol_pc",
) -> NormalizedItem:
    blocks = [{"id": "b0001", "type": "paragraph", "text": title}]
    if quoted_url:
        blocks.append({"id": "b0002", "type": "embed", "embed_kind": "quoted_post", "source_url": quoted_url})
    raw_item = RawItem(
        source_id=source.id,
        external_id=external_id,
        native_title=title,
        canonical_url=f"https://example.com/{external_id}",
        language="en",
        content_blocks=blocks,
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
        entities=[{"name": "26.13", "type": "patch"}],
        primary_topic=primary_topic,
        subtopic=subtopic,
        source_kind="first_party" if source.is_official else "attributed_report",
        information_stage="preview",
        product_scope=product_scope,
        importance_score=0.9,
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
            aggregation_key="patch:lol_pc:26.13",
            title="英雄联盟 26.13 版本预览",
            summary="设计师发布了 26.13 版本预览。",
            event_kind="gameplay_update",
            aggregation_strategy="patch_cycle",
            product_scope="lol_pc",
            evidence={"match": "patch_key"},
        )
        event, added = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=full_preview.id,
            title="英雄联盟 26.13 版本完整预览",
            summary="设计师补充了 26.13 版本的完整改动。",
            evidence={"match": "patch:lol_pc:26.13"},
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
            aggregation_key="patch:lol_pc:26.13",
            title="26.13 版本预览",
            summary="初始摘要",
            event_kind="gameplay_update",
            aggregation_strategy="patch_cycle",
            product_scope="lol_pc",
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
            event_kind="other",
            aggregation_strategy="singleton",
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
        assert db.get(EventClaim, (event.id, previous_claim.id)) is not None
        assert db.get(EventClaim, (event.id, replacement_claim.id)) is not None
        previous_membership = db.scalar(
            select(EventMessage).where(
                EventMessage.normalized_item_id == previous.id
            )
        )
        assert previous_membership is not None
        assert previous_membership.membership_status == "withdrawn"
        assert previous_claim.status == "superseded"
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
            event_kind="other",
            aggregation_strategy="singleton",
        )
        event_two = create_event(
            db,
            normalized_item_id=second.id,
            title="Event Two",
            summary="Two",
            event_kind="other",
            aggregation_strategy="singleton",
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
        source = Source(name="API Source", connector_type="manual", is_official=True)
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
            aggregation_key="test:api",
            title="API Event",
            summary="API summary",
            event_kind="other",
            aggregation_strategy="singleton",
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
                "/api/v1/events?event_kind=other&lifecycle_status=confirmed&limit=1&offset=0"
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
    assert listing.json()[0]["aggregation_key"] == "test:api"
    assert listing.json()[0]["message_count"] == 3
    assert filtered.status_code == 200
    assert event_page.status_code == 200
    assert event_page.json()["total"] == 1
    assert event_page.json()["items"][0]["id"] == event_id
    assert message_page.status_code == 200
    assert message_page.json()["total"] == 1
    assert message_page.json()["items"][0]["id"] == newer_item_id
    assert [row["aggregation_key"] for row in filtered.json()] == ["test:api"]
    assert detail.status_code == 200
    assert detail.json()["revisions"][0]["revision"] == 1
    assert detail.json()["messages"][0]["timeline_note"]
    assert detail.json()["messages"][0]["update_kind"] == "new_fact"
    assert [message["title"] for message in detail.json()["messages"]] == [
        "API Item",
        "API Item Newer",
        "API Item Without Publish Time",
    ]
    assert messages.status_code == 200
    assert [message["title"] for message in messages.json()] == [
        "API Item",
        "API Item Newer",
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
        first_source = Source(name="Reporter One", connector_type="x_twitter", reliability_score=0.6)
        second_source = Source(name="Reporter Two", connector_type="weibo", reliability_score=0.6)
        db.add_all([first_source, second_source])
        db.commit()
        first = _add_normalized_item(
            db,
            source=first_source,
            external_id="rumor-1",
            title="传闻：选手加入战队",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
        same_source = _add_normalized_item(
            db,
            source=first_source,
            external_id="rumor-2",
            title="同一记者补充转会消息",
            published_at=datetime(2026, 7, 2, tzinfo=UTC),
        )
        independent = _add_normalized_item(
            db,
            source=second_source,
            external_id="rumor-3",
            title="第二名记者独立印证",
            published_at=datetime(2026, 7, 3, tzinfo=UTC),
        )

        event = create_event(
            db,
            normalized_item_id=first.id,
            title="传闻：选手加入战队",
            summary="单源转会爆料。",
            event_kind="roster_change",
            aggregation_strategy="timeline",
            product_scope="lol_esports",
            lifecycle_status="unconfirmed",
        )
        assert event.credibility_status == "single_source"
        assert event.credibility_score == pytest.approx(0.6)

        event, _ = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=same_source.id,
            is_significant_update=False,
        )
        assert event.independent_source_count == 1
        assert event.credibility_score == pytest.approx(0.6)
        assert event.current_revision == 1

        event, _ = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=independent.id,
            is_significant_update=False,
        )
        assert event.independent_source_count == 2
        assert event.credibility_status == "multi_source_supported"
        assert event.credibility_score == pytest.approx(0.7)
        assert event.current_revision == 1


def test_event_credibility_caps_nonofficial_source_at_ninety_percent() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Reliable insider", connector_type="x_twitter", reliability_score=0.95)
        db.add(source)
        db.commit()
        item = _add_normalized_item(
            db,
            source=source,
            external_id="high-confidence",
            title="高确定性爆料",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
        )

        event = create_event(
            db,
            normalized_item_id=item.id,
            title="传闻事件",
            summary="单一高确定性信源。",
            event_kind="roster_change",
            aggregation_strategy="timeline",
            product_scope="lol_esports",
            lifecycle_status="unconfirmed",
        )

        assert event.credibility_score == pytest.approx(0.9)


def test_official_confirmation_overrides_event_credibility() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        reporter = Source(name="Reporter", connector_type="x_twitter", reliability_score=0.5)
        team = Source(name="Team Official", connector_type="weibo", is_official=True, reliability_score=1)
        db.add_all([reporter, team])
        db.commit()
        rumor = _add_normalized_item(
            db,
            source=reporter,
            external_id="rumor",
            title="传闻：选手加入战队",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
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
            event_kind="roster_change",
            aggregation_strategy="timeline",
            product_scope="lol_esports",
            lifecycle_status="unconfirmed",
        )
        event, _ = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=announcement.id,
            title="战队官宣选手加入",
            lifecycle_status="confirmed",
            change_note="战队正式官宣",
        )

        assert event.credibility_status == "official_confirmed"
        assert event.credibility_score == 1
        assert event.lifecycle_status == "confirmed"
        assert event.official_source_count == 1


def test_official_refutation_and_official_conflict_have_priority() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        official = Source(name="Official", is_official=True, reliability_score=1)
        db.add(official)
        db.commit()
        denial = _add_normalized_item(db, source=official, external_id="denial", title="官方否认", published_at=datetime(2026, 7, 1, tzinfo=UTC))
        support = _add_normalized_item(db, source=official, external_id="support", title="官方支持", published_at=datetime(2026, 7, 2, tzinfo=UTC))
        event = create_event(db, normalized_item_id=denial.id, title="争议事件", summary="官方否认", event_kind="roster_change", aggregation_strategy="timeline", product_scope="lol_esports", lifecycle_status="unconfirmed", evidence_stance="contradicts", update_kind="refutation")
        assert event.credibility_status == "officially_refuted"
        assert event.credibility_score == 0
        event, _ = add_message_to_event(db, event_id=event.id, normalized_item_id=support.id, evidence_stance="supports", update_kind="confirmation")
        assert event.credibility_status == "disputed"
        assert event.credibility_score == 0.5


def test_official_repost_is_not_official_evidence_and_shared_upstream_is_one_source() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        first_source = Source(name="Official repost", is_official=True, reliability_score=1)
        second_source = Source(name="Another repost", reliability_score=0.8)
        db.add_all([first_source, second_source])
        db.commit()
        upstream = "https://x.com/original/status/123?ref=share"
        first = _add_normalized_item(db, source=first_source, external_id="repost-1", title="转载原帖", published_at=datetime(2026, 7, 1, tzinfo=UTC), quoted_url=upstream)
        second = _add_normalized_item(db, source=second_source, external_id="repost-2", title="再次转载", published_at=datetime(2026, 7, 2, tzinfo=UTC), quoted_url="https://www.x.com/original/status/123")
        event = create_event(db, normalized_item_id=first.id, title="转载事件", summary="两次转载", event_kind="roster_change", aggregation_strategy="timeline", product_scope="lol_esports", lifecycle_status="unconfirmed")
        event, _ = add_message_to_event(db, event_id=event.id, normalized_item_id=second.id)
        assert event.official_source_count == 0
        assert event.independent_source_count == 1
        assert event.credibility_status == "single_source"
        assert event.credibility_score == 0.9


def test_three_independent_sources_add_two_tenths_and_conflict_is_disputed() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        sources = [Source(name=f"Source {index}", reliability_score=0.55) for index in range(4)]
        db.add_all(sources)
        db.commit()
        items = [_add_normalized_item(db, source=source, external_id=f"s-{index}", title=f"证据 {index}", published_at=datetime(2026, 7, index + 1, tzinfo=UTC)) for index, source in enumerate(sources)]
        event = create_event(db, normalized_item_id=items[0].id, title="多源事件", summary="多源支持", event_kind="roster_change", aggregation_strategy="timeline", product_scope="lol_esports", lifecycle_status="unconfirmed")
        for item in items[1:3]:
            event, _ = add_message_to_event(db, event_id=event.id, normalized_item_id=item.id)
        assert event.supporting_source_count == 3
        assert event.credibility_score == pytest.approx(0.75)
        event, _ = add_message_to_event(db, event_id=event.id, normalized_item_id=items[3].id, evidence_stance="contradicts")
        assert event.contradicting_source_count == 1
        assert event.credibility_status == "disputed"
        assert event.credibility_score == 0.5


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
            aggregation_key="matchday:lpl:2026-07-26",
            title="7月26日赛果",
            summary="三场比赛已经结束。",
            event_kind="esports_match",
            aggregation_strategy="calendar_day",
            product_scope="lol_esports",
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
            event_kind="community_activity",
            aggregation_strategy="singleton",
            product_scope="lol_pc",
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
        memberships = list(
            db.scalars(
                select(EventMessage).where(
                    EventMessage.event_id == event.id
                ).order_by(EventMessage.normalized_item_id)
            )
        )
        assert [
            (membership.normalized_item_id, membership.membership_status)
            for membership in memberships
        ] == [(old.id, "withdrawn"), (new.id, "active")]
        assert db.scalar(
            select(func.count(EventRevision.id)).where(
                EventRevision.event_id == event.id
            )
        ) == 1


def test_event_importance_is_led_by_member_contributions() -> None:
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
            primary_topic="commerce",
            subtopic="shop_rotation",
        )
        daily = _add_normalized_item(
            db,
            source=source,
            external_id="daily",
            title="神话商城每日轮换",
            published_at=datetime(2026, 7, 24, tzinfo=UTC),
            primary_topic="commerce",
            subtopic="shop_rotation",
        )
        event = create_event(
            db,
            normalized_item_id=weekly.id,
            aggregation_key="shop_rotation:lol_pc:cn:2026-W30",
            title="2026年第30周国服神话商城轮换",
            summary="本周轮换。",
            event_kind="commercial_offer",
            aggregation_strategy="recurring_window",
            product_scope="lol_pc",
        )
        event, _ = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=daily.id,
            is_significant_update=False,
        )

        assert weekly.importance_score == 0.9
        assert daily.importance_score == 0.9
        assert event.importance_score == 0.9
        assert event.importance_dimensions["event_kind"] == "commercial_offer"
        assert event.importance_dimensions["member_evidence_signal"] == 0.9
        assert event.importance_dimensions["breadth_boost"] == 0
        assert event.current_revision == 1

        component_event = create_event(
            db,
            normalized_item_id=weekly.id,
            title="轮换中的外观发布",
            summary="作为商城消息中的独立外观事件。",
            event_kind="cosmetic_release",
            aggregation_strategy="release",
            product_scope="lol_pc",
            membership_role="component",
            update_kind="duplicate_evidence",
        )
        component = db.get(EventMessage, (component_event.id, weekly.id))
        assert component.importance_contribution == 0.68
        assert component_event.importance_score == 0.68


def test_global_shop_rotation_is_lower_than_cn_rotation() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Global Rotation", connector_type="x_twitter")
        db.add(source)
        db.commit()
        item = _add_normalized_item(
            db,
            source=source,
            external_id="global-weekly",
            title="Mythic Shop weekly rotation",
            published_at=datetime(2026, 7, 23, tzinfo=UTC),
            primary_topic="commerce",
            subtopic="shop_rotation",
        )
        event = create_event(
            db,
            normalized_item_id=item.id,
            aggregation_key="shop_rotation:lol_pc:global:2026-W30",
            title="2026 W30 global Mythic Shop rotation",
            summary="Global rotation.",
            event_kind="commercial_offer",
            aggregation_strategy="recurring_window",
            product_scope="lol_pc",
        )

        assert item.importance_score == 0.9
        assert event.importance_score == 0.78
        assert event.importance_dimensions["market_reach_modifier"] == -0.12
        assert event.importance_policy_version == "event-importance-v5-component-baselines"


def test_event_importance_does_not_increase_with_source_count() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        first_source = Source(name="First report", connector_type="weibo")
        second_source = Source(name="Second report", connector_type="weibo")
        db.add_all([first_source, second_source])
        db.commit()
        first = _add_normalized_item(
            db,
            source=first_source,
            external_id="first-report",
            title="同一事件的第一条消息",
            published_at=datetime(2026, 7, 23, tzinfo=UTC),
        )
        second = _add_normalized_item(
            db,
            source=second_source,
            external_id="second-report",
            title="同一事件的第二条消息",
            published_at=datetime(2026, 7, 24, tzinfo=UTC),
        )
        event = create_event(
            db,
            normalized_item_id=first.id,
            title="同一事件",
            summary="第一条消息。",
            event_kind="gameplay_update",
            aggregation_strategy="timeline",
            product_scope="lol_pc",
        )
        event, _ = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=second.id,
        )

        assert event.independent_source_count == 2
        assert event.importance_score == first.importance_score == 0.9
        assert event.importance_dimensions["breadth_boost"] == 0
        assert event.importance_policy_version == "event-importance-v5-component-baselines"


def test_unconfirmed_timeline_expires_idempotently_without_decay() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Rumor Source", connector_type="weibo", reliability_score=0.6)
        db.add(source)
        db.commit()
        rumor = _add_normalized_item(
            db,
            source=source,
            external_id="stale-rumor",
            title="传闻：WBG 正在考虑新的打野候选",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
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
        assert event.credibility_score == pytest.approx(0.6)
        assert event.current_revision == 2

        repeated = expire_stale_unconfirmed_events(
            db,
            as_of=datetime(2026, 7, 29, tzinfo=UTC),
        )
        assert repeated == [event.id]
        assert event.credibility_score == pytest.approx(0.6)
        assert event.current_revision == 2

        official_source = Source(
            name="Riot official",
            connector_type="riot_official",
            is_official=True,
            reliability_score=1,
        )
        db.add(official_source)
        db.commit()
        confirmation = _add_normalized_item(
            db,
            source=official_source,
            external_id="official-confirmation-after-expiry",
            title="WBG 官宣新打野",
            published_at=datetime(2026, 7, 30, tzinfo=UTC),
        )
        add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=confirmation.id,
            update_kind="confirmation",
        )

        assert event.lifecycle_status == "confirmed"
        assert event.credibility_status == "official_confirmed"
        assert event.credibility_score == 1
