from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base, get_db
from app.main import app
from app.models.event import EventMessage, EventRevision
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.services.event_aggregation import (
    EventMembershipConflictError,
    add_message_to_event,
    create_event,
)


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
) -> NormalizedItem:
    raw_item = RawItem(
        source_id=source.id,
        external_id=external_id,
        native_title=title,
        canonical_url=f"https://example.com/{external_id}",
        language="en",
        content_blocks=[{"id": "b0001", "type": "paragraph", "text": title}],
        published_at=published_at,
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
        credibility="official",
        credibility_score=1.0,
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
def test_message_cannot_belong_to_two_events() -> None:
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

        with pytest.raises(EventMembershipConflictError):
            add_message_to_event(
                db,
                event_id=event_two.id,
                normalized_item_id=first.id,
            )

        db.add(
            EventMessage(
                event_id=event_two.id,
                normalized_item_id=first.id,
                relation_type="primary",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


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

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            listing = client.get("/api/v1/events")
            detail = client.get(f"/api/v1/events/{event_id}")
            messages = client.get(f"/api/v1/events/{event_id}/messages")
            missing = client.get("/api/v1/events/9999")
    finally:
        app.dependency_overrides.clear()

    assert listing.status_code == 200
    assert listing.json()[0]["event_key"] == "test:api"
    assert listing.json()[0]["message_count"] == 3
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
    assert missing.status_code == 404
    assert missing.json()["detail"] == "event not found"


def test_event_membership_uses_restrict_delete_policy() -> None:
    foreign_key = next(iter(EventMessage.__table__.c.normalized_item_id.foreign_keys))
    assert foreign_key.ondelete == "RESTRICT"
