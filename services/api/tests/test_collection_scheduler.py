import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
import app.services.collection_scheduler as scheduler_service
from app.core.database import Base, get_db
from app.core.config import settings
from app.main import app
from app.models.collection_schedule import SourceCollectionSchedule
from app.models.connector_run import ConnectorRun
from app.models.source import Source
from app.schemas.collection_schedule import CollectionScheduleUpdate
from app.services.connector_runner import ConnectorRunError
from app.services.collection_scheduler import (
    claim_due_schedule,
    execute_claimed_schedule,
    request_collection_run,
    upsert_collection_schedule,
)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


def _source(
    db: Session,
    *,
    name: str = "Scheduled Riot",
    connector_type: str = "riot_official",
    is_active: bool = True,
) -> Source:
    source = Source(
        name=name,
        connector_type=connector_type,
        is_active=is_active,
    )
    db.add(source)
    db.commit()
    return source


def test_schedule_validation_and_run_request(db: Session) -> None:
    source = _source(db)
    schedule = upsert_collection_schedule(
        db,
        source=source,
        payload=CollectionScheduleUpdate(
            enabled=True,
            interval_minutes=120,
            retry_delay_minutes=10,
            fetch_limit=6,
        ),
    )
    assert schedule.enabled is True
    assert schedule.next_run_at is not None
    assert schedule.fetch_limit == 6

    schedule.enabled = False
    schedule.next_run_at = None
    db.commit()
    requested = request_collection_run(db, source=source)
    assert requested.enabled is False
    assert requested.run_requested_at is not None

    manual = _source(db, name="Manual", connector_type="manual")
    with pytest.raises(ValueError, match="manual sources"):
        request_collection_run(db, source=manual)

    inactive = _source(db, name="Inactive", is_active=False)
    with pytest.raises(ValueError, match="inactive sources"):
        upsert_collection_schedule(
            db,
            source=inactive,
            payload=CollectionScheduleUpdate(enabled=True),
        )

    tencent = _source(db, name="Tencent", connector_type="tencent_lol")
    with pytest.raises(ValueError, match="unsupported"):
        upsert_collection_schedule(
            db,
            source=tencent,
            payload=CollectionScheduleUpdate(
                enabled=False,
                options={"unknown": "value"},
            ),
        )


def test_claim_is_exclusive_and_expired_lease_is_recovered(db: Session) -> None:
    source = _source(db)
    upsert_collection_schedule(
        db,
        source=source,
        payload=CollectionScheduleUpdate(enabled=True),
    )

    first_claim = claim_due_schedule(db)
    assert first_claim is not None
    schedule_id, first_token = first_claim
    assert claim_due_schedule(db) is None

    schedule = db.get(SourceCollectionSchedule, schedule_id)
    assert schedule is not None
    schedule.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()

    second_claim = claim_due_schedule(db)
    assert second_claim is not None
    assert second_claim[0] == schedule_id
    assert second_claim[1] != first_token


@pytest.mark.anyio
async def test_successful_run_uses_watermark_and_schedules_next_run(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(db)
    previous_success = datetime(2026, 7, 26, 8, 30, tzinfo=UTC)
    schedule = upsert_collection_schedule(
        db,
        source=source,
        payload=CollectionScheduleUpdate(
            enabled=True,
            interval_minutes=90,
            retry_delay_minutes=7,
            fetch_limit=4,
        ),
    )
    schedule.last_success_at = previous_success
    db.commit()
    received: dict[str, object] = {}

    async def fake_run_connector(
        session: Session,
        **kwargs: object,
    ) -> ConnectorRun:
        received.update(kwargs)
        run = ConnectorRun(
            source_id=source.id,
            connector_type=source.connector_type,
            status="completed",
            finished_at=datetime.now(UTC),
        )
        session.add(run)
        session.commit()
        return run

    monkeypatch.setattr(scheduler_service, "run_connector", fake_run_connector)
    claim = claim_due_schedule(db)
    assert claim is not None
    await execute_claimed_schedule(
        db,
        schedule_id=claim[0],
        lease_token=claim[1],
    )

    db.expire_all()
    finished = db.get(SourceCollectionSchedule, schedule.id)
    assert finished is not None
    assert finished.last_status == "succeeded"
    assert finished.last_connector_run_id is not None
    assert finished.lease_token is None
    assert finished.next_run_at is not None
    assert received["limit"] == 4
    assert received["since"].replace(tzinfo=UTC) == previous_success - timedelta(
        minutes=10
    )


@pytest.mark.anyio
async def test_failed_run_records_error_and_uses_retry_delay(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(db)
    schedule = upsert_collection_schedule(
        db,
        source=source,
        payload=CollectionScheduleUpdate(
            enabled=True,
            interval_minutes=60,
            retry_delay_minutes=8,
        ),
    )
    schedule.collection_cursor = {
        "version": 1,
        "watermark": "2026-07-26T08:30:00+00:00",
        "pending_ids": ["already-stored"],
    }
    db.commit()

    async def fake_run_connector(
        session: Session,
        **_: object,
    ) -> ConnectorRun:
        run = ConnectorRun(
            source_id=source.id,
            connector_type=source.connector_type,
            status="failed",
            error_message="upstream unavailable",
            finished_at=datetime.now(UTC),
        )
        session.add(run)
        session.commit()
        raise ConnectorRunError("upstream unavailable")

    monkeypatch.setattr(scheduler_service, "run_connector", fake_run_connector)
    claim = claim_due_schedule(db)
    assert claim is not None
    before = datetime.now(UTC)
    await execute_claimed_schedule(
        db,
        schedule_id=claim[0],
        lease_token=claim[1],
    )

    db.expire_all()
    failed = db.get(SourceCollectionSchedule, schedule.id)
    assert failed is not None
    assert failed.last_status == "failed"
    assert failed.last_error == "upstream unavailable"
    assert failed.collection_cursor["pending_ids"] == ["already-stored"]
    assert failed.lease_token is None
    assert failed.next_run_at is not None
    next_run = failed.next_run_at.replace(tzinfo=UTC)
    assert next_run >= before + timedelta(minutes=7, seconds=50)


@pytest.mark.anyio
async def test_lost_lease_does_not_overwrite_new_owner(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(db)
    schedule = upsert_collection_schedule(
        db,
        source=source,
        payload=CollectionScheduleUpdate(enabled=True),
    )
    claim = claim_due_schedule(db)
    assert claim is not None

    async def fake_run_connector(
        session: Session,
        **_: object,
    ) -> ConnectorRun:
        run = ConnectorRun(
            source_id=source.id,
            connector_type=source.connector_type,
            status="completed",
            finished_at=datetime.now(UTC),
        )
        session.add(run)
        session.commit()
        session.execute(
            scheduler_service.update(SourceCollectionSchedule)
            .where(SourceCollectionSchedule.id == schedule.id)
            .values(
                lease_token="new-owner-token",
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        session.commit()
        return run

    monkeypatch.setattr(scheduler_service, "run_connector", fake_run_connector)
    await execute_claimed_schedule(
        db,
        schedule_id=claim[0],
        lease_token=claim[1],
    )

    db.expire_all()
    current = db.get(SourceCollectionSchedule, schedule.id)
    assert current is not None
    assert current.lease_token == "new-owner-token"
    assert current.last_status == "running"


@pytest.mark.anyio
async def test_connector_timeout_fails_run_releases_lease_and_allows_next_source(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stuck_source = _source(db, name="Stuck source")
    healthy_source = _source(db, name="Healthy source")
    stuck_schedule = upsert_collection_schedule(
        db,
        source=stuck_source,
        payload=CollectionScheduleUpdate(enabled=True, retry_delay_minutes=8),
    )
    healthy_schedule = upsert_collection_schedule(
        db,
        source=healthy_source,
        payload=CollectionScheduleUpdate(enabled=True),
    )
    monkeypatch.setattr(settings, "collection_run_timeout_seconds", 0.01)

    async def fake_run_connector(session: Session, **kwargs: object) -> ConnectorRun:
        source_id = int(kwargs["source_id"])
        run = ConnectorRun(
            source_id=source_id,
            connector_type="riot_official",
            status="running",
        )
        session.add(run)
        session.commit()
        if source_id == stuck_source.id:
            await asyncio.sleep(1)
        run.status = "completed"
        run.finished_at = datetime.now(UTC)
        session.commit()
        return run

    monkeypatch.setattr(scheduler_service, "run_connector", fake_run_connector)

    first_claim = claim_due_schedule(db)
    assert first_claim is not None
    await execute_claimed_schedule(
        db,
        schedule_id=first_claim[0],
        lease_token=first_claim[1],
    )
    db.expire_all()
    failed = db.get(SourceCollectionSchedule, stuck_schedule.id)
    assert failed is not None
    assert failed.last_status == "failed"
    assert failed.last_error == "connector run exceeded deadline of 0.01 seconds"
    assert failed.consecutive_failures == 1
    assert failed.lease_token is None
    assert failed.next_run_at is not None
    failed_run = db.get(ConnectorRun, failed.last_connector_run_id)
    assert failed_run is not None
    assert failed_run.status == "failed"

    second_claim = claim_due_schedule(db)
    assert second_claim is not None
    await execute_claimed_schedule(
        db,
        schedule_id=second_claim[0],
        lease_token=second_claim[1],
    )
    db.expire_all()
    healthy = db.get(SourceCollectionSchedule, healthy_schedule.id)
    assert healthy is not None
    assert healthy.last_status == "succeeded"


def test_collection_schedule_api_lists_configures_and_requests_run() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        source = _source(session)
        source_id = source.id

    def override_get_db():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            configured = client.put(
                f"/api/v1/collection-schedules/sources/{source_id}",
                json={
                    "enabled": False,
                    "interval_minutes": 45,
                    "retry_delay_minutes": 9,
                    "fetch_limit": 3,
                    "options": {},
                },
            )
            assert configured.status_code == 200
            assert configured.json()["interval_minutes"] == 45

            listed = client.get("/api/v1/collection-schedules")
            assert listed.status_code == 200
            assert listed.json()[0]["source_name"] == "Scheduled Riot"

            requested = client.post(
                f"/api/v1/collection-schedules/sources/{source_id}/run-now"
            )
            assert requested.status_code == 200
            assert requested.json()["run_requested_at"] is not None
    finally:
        app.dependency_overrides.clear()
