from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base, get_db
from app.main import app
from app.models.event import EventRevision
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.services.digests import generate_digest
from app.services.event_aggregation import create_event


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


def _as_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=UTC)
        if value.tzinfo is None
        else value.astimezone(UTC)
    )


def _event(db: Session):
    source = Source(name="Digest timezone source", connector_type="manual")
    db.add(source)
    db.flush()
    raw_item = RawItem(
        source_id=source.id,
        content_blocks=[{"type": "paragraph", "text": "Digest input"}],
        published_at=datetime(2026, 8, 3, tzinfo=UTC),
    )
    db.add(raw_item)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw_item.id,
        normalized_title="Digest input",
        normalized_text="Digest input",
        summary="Digest input",
        category="news",
        entities=[],
        importance_score=0.5,
        analysis_model="test",
    )
    db.add(item)
    db.commit()
    return create_event(
        db,
        normalized_item_id=item.id,
        title="Digest event",
        summary="Digest event summary",
        category="news",
    )


def test_naive_cutoff_is_interpreted_in_shanghai_and_normalized_to_utc(
    db: Session,
) -> None:
    digest = generate_digest(
        db,
        digest_type="daily",
        cutoff_at=datetime(2026, 8, 4),
        timezone="Asia/Shanghai",
    )
    assert _as_utc(digest.cutoff_at) == datetime(2026, 8, 3, 16, tzinfo=UTC)
    assert _as_utc(digest.window_start) == datetime(
        2026, 8, 2, 16, tzinfo=UTC
    )
    assert digest.title.endswith("2026-08-04")


def test_aware_cutoff_converts_to_local_date_and_is_idempotent(
    db: Session,
) -> None:
    naive = generate_digest(
        db,
        digest_type="daily",
        cutoff_at=datetime(2026, 8, 4),
        timezone="Asia/Shanghai",
    )
    aware = generate_digest(
        db,
        digest_type="daily",
        cutoff_at=datetime(2026, 8, 3, 16, tzinfo=UTC),
        timezone="Asia/Shanghai",
    )
    assert aware.id == naive.id
    assert aware.current_revision == 1
    assert aware.title.endswith("2026-08-04")


def test_daily_window_uses_local_day_across_dst_transition(db: Session) -> None:
    digest = generate_digest(
        db,
        digest_type="daily",
        cutoff_at=datetime(2026, 3, 9),
        timezone="America/New_York",
    )
    start = _as_utc(digest.window_start)
    cutoff = _as_utc(digest.cutoff_at)
    assert start == datetime(2026, 3, 8, 5, tzinfo=UTC)
    assert cutoff == datetime(2026, 3, 9, 4, tzinfo=UTC)
    assert (cutoff - start).total_seconds() == 23 * 60 * 60


def test_weekly_window_uses_seven_local_days(db: Session) -> None:
    digest = generate_digest(
        db,
        digest_type="weekly",
        cutoff_at=datetime(2026, 8, 8),
        timezone="Asia/Shanghai",
    )
    assert _as_utc(digest.window_start) == datetime(
        2026, 7, 31, 16, tzinfo=UTC
    )
    assert _as_utc(digest.cutoff_at) == datetime(
        2026, 8, 7, 16, tzinfo=UTC
    )


def test_invalid_timezone_is_a_clear_422(db: Session) -> None:
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(app).post(
            "/api/v1/digests/generate",
            params={
                "digest_type": "daily",
                "cutoff_at": "2026-08-04T00:00:00",
                "timezone": "Mars/Olympus",
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert "invalid IANA timezone" in response.json()["detail"]


def test_late_event_revision_increments_digest_revision(db: Session) -> None:
    event = _event(db)
    initial_revision = db.scalar(
        select(EventRevision).where(
            EventRevision.event_id == event.id,
            EventRevision.revision == 1,
        )
    )
    assert initial_revision is not None
    initial_revision.created_at = datetime(2026, 8, 3, 1, tzinfo=UTC)
    db.commit()
    cutoff = datetime(2026, 8, 4)
    digest = generate_digest(
        db,
        digest_type="daily",
        cutoff_at=cutoff,
        timezone="Asia/Shanghai",
    )
    assert digest.current_revision == 1

    db.add(
        EventRevision(
            event_id=event.id,
            revision=2,
            title="Late digest correction",
            summary="Late correction summary",
            change_note="late",
            evidence_snapshot={},
            created_at=datetime(2026, 8, 3, 2, tzinfo=UTC),
        )
    )
    db.commit()
    revised = generate_digest(
        db,
        digest_type="daily",
        cutoff_at=cutoff,
        timezone="Asia/Shanghai",
    )
    assert revised.current_revision == 2
    assert "Late digest correction" in revised.body
