import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.connectors.registry import connector_registry
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.collection_schedule import SourceCollectionSchedule
from app.models.connector_run import ConnectorRun
from app.models.source import Source
from app.schemas.collection_schedule import CollectionScheduleUpdate
from app.services.connector_runner import run_connector


def _validate_source(source: Source, *, require_active: bool) -> None:
    if source.connector_type == "manual":
        raise ValueError("manual sources cannot have collection schedules")
    connector_registry.create(source.connector_type)
    if require_active and not source.is_active:
        raise ValueError("inactive sources cannot be scheduled or run")


def _validate_options(source: Source, options: dict[str, Any]) -> None:
    connector = connector_registry.create(source.connector_type)
    unsupported = set(options) - connector.allowed_run_options
    if unsupported:
        raise ValueError(
            f"unsupported {source.connector_type} run options: {sorted(unsupported)}"
        )


def list_collection_schedules(db: Session) -> list[SourceCollectionSchedule]:
    return list(
        db.scalars(
            select(SourceCollectionSchedule)
            .options(selectinload(SourceCollectionSchedule.source))
            .order_by(SourceCollectionSchedule.source_id)
        )
    )


def upsert_collection_schedule(
    db: Session,
    *,
    source: Source,
    payload: CollectionScheduleUpdate,
) -> SourceCollectionSchedule:
    _validate_source(source, require_active=payload.enabled)
    _validate_options(source, payload.options)
    schedule = db.scalar(
        select(SourceCollectionSchedule).where(
            SourceCollectionSchedule.source_id == source.id
        )
    )
    was_enabled = schedule.enabled if schedule is not None else False
    if schedule is None:
        schedule = SourceCollectionSchedule(source_id=source.id)
        db.add(schedule)
    schedule.enabled = payload.enabled
    schedule.interval_minutes = payload.interval_minutes
    schedule.retry_delay_minutes = payload.retry_delay_minutes
    schedule.fetch_limit = payload.fetch_limit
    schedule.options = payload.options
    schedule.overlap_minutes = payload.overlap_minutes
    if payload.enabled and (not was_enabled or schedule.next_run_at is None):
        schedule.next_run_at = datetime.now(UTC)
    elif not payload.enabled:
        schedule.next_run_at = None
    db.commit()
    return db.scalar(
        select(SourceCollectionSchedule)
        .where(SourceCollectionSchedule.id == schedule.id)
        .options(selectinload(SourceCollectionSchedule.source))
    )


def request_collection_run(
    db: Session,
    *,
    source: Source,
) -> SourceCollectionSchedule:
    _validate_source(source, require_active=True)
    schedule = db.scalar(
        select(SourceCollectionSchedule).where(
            SourceCollectionSchedule.source_id == source.id
        )
    )
    if schedule is None:
        schedule = SourceCollectionSchedule(
            source_id=source.id,
            enabled=False,
        )
        db.add(schedule)
    _validate_options(source, schedule.options)
    schedule.run_requested_at = datetime.now(UTC)
    db.commit()
    return db.scalar(
        select(SourceCollectionSchedule)
        .where(SourceCollectionSchedule.id == schedule.id)
        .options(selectinload(SourceCollectionSchedule.source))
    )


def claim_due_schedule(db: Session) -> tuple[int, str] | None:
    now = datetime.now(UTC)
    schedule = db.scalar(
        select(SourceCollectionSchedule)
        .join(SourceCollectionSchedule.source)
        .where(
            Source.is_active.is_(True),
            Source.connector_type != "manual",
            or_(
                SourceCollectionSchedule.run_requested_at.is_not(None),
                and_(
                    SourceCollectionSchedule.enabled.is_(True),
                    SourceCollectionSchedule.next_run_at.is_not(None),
                    SourceCollectionSchedule.next_run_at <= now,
                ),
                and_(
                    SourceCollectionSchedule.last_status == "running",
                    or_(
                        SourceCollectionSchedule.lease_expires_at.is_(None),
                        SourceCollectionSchedule.lease_expires_at <= now,
                    ),
                ),
            ),
            or_(
                SourceCollectionSchedule.last_status != "running",
                SourceCollectionSchedule.lease_expires_at.is_(None),
                SourceCollectionSchedule.lease_expires_at <= now,
            ),
        )
        .order_by(
            SourceCollectionSchedule.run_requested_at.desc().nullslast(),
            SourceCollectionSchedule.next_run_at,
            SourceCollectionSchedule.id,
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if schedule is None:
        return None
    token = secrets.token_hex(24)
    schedule.last_status = "running"
    schedule.last_started_at = now
    schedule.last_error = None
    schedule.lease_token = token
    schedule.lease_expires_at = now + timedelta(
        minutes=settings.collection_scheduler_lease_minutes
    )
    schedule.run_requested_at = None
    db.commit()
    return schedule.id, token


def _latest_connector_run(db: Session, source_id: int) -> ConnectorRun | None:
    return db.scalar(
        select(ConnectorRun)
        .where(ConnectorRun.source_id == source_id)
        .order_by(ConnectorRun.id.desc())
        .limit(1)
    )


async def execute_claimed_schedule(
    db: Session,
    *,
    schedule_id: int,
    lease_token: str,
) -> None:
    schedule = db.scalar(
        select(SourceCollectionSchedule)
        .where(SourceCollectionSchedule.id == schedule_id)
        .options(selectinload(SourceCollectionSchedule.source))
    )
    if (
        schedule is None
        or schedule.last_status != "running"
        or schedule.lease_token != lease_token
    ):
        raise ValueError("collection schedule lease is no longer owned")
    source_id = schedule.source_id
    connector_type = schedule.source.connector_type
    fetch_limit = schedule.fetch_limit
    options = dict(schedule.options)
    cursor = dict(schedule.collection_cursor or {})
    watermark_value = cursor.get("watermark")
    try:
        watermark = (
            datetime.fromisoformat(watermark_value)
            if isinstance(watermark_value, str)
            else None
        )
    except ValueError:
        watermark = None
    if watermark is None:
        watermark = schedule.last_success_at
    since = (
        watermark - timedelta(minutes=schedule.overlap_minutes)
        if watermark is not None
        else None
    )

    heartbeat = asyncio.create_task(
        _heartbeat_schedule(schedule_id, lease_token)
    )
    succeeded = False
    error_message = None
    try:
        run = await run_connector(
            db,
            connector_type=connector_type,
            source_id=source_id,
            limit=fetch_limit,
            since=since,
            options=options,
            cursor=cursor,
        )
        succeeded = True
    except Exception as exc:
        error_message = str(exc)[:4000]
        run = _latest_connector_run(db, source_id)
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)

    now = datetime.now(UTC)
    schedule = db.get(SourceCollectionSchedule, schedule_id)
    if schedule is None or schedule.lease_token != lease_token:
        return
    schedule.last_connector_run_id = run.id if run is not None else None
    schedule.last_finished_at = now
    schedule.last_status = "succeeded" if succeeded else "failed"
    schedule.last_error = error_message
    if succeeded:
        schedule.last_success_at = run.finished_at or now
        schedule.collection_cursor = dict(run.next_cursor or cursor)
        schedule.consecutive_failures = 0
    else:
        schedule.consecutive_failures += 1
    delay = (
        (1 if succeeded and run is not None and run.truncated else schedule.interval_minutes)
        if succeeded
        else schedule.retry_delay_minutes
    )
    schedule.next_run_at = (
        now + timedelta(minutes=delay)
        if schedule.enabled
        else None
    )
    schedule.lease_token = None
    schedule.lease_expires_at = None
    db.commit()


def _renew_schedule_lease(schedule_id: int, lease_token: str) -> bool:
    now = datetime.now(UTC)
    with SessionLocal() as db:
        result = db.execute(
            update(SourceCollectionSchedule)
            .where(
                SourceCollectionSchedule.id == schedule_id,
                SourceCollectionSchedule.last_status == "running",
                SourceCollectionSchedule.lease_token == lease_token,
            )
            .values(
                lease_expires_at=now
                + timedelta(minutes=settings.collection_scheduler_lease_minutes)
            )
        )
        db.commit()
        return bool(result.rowcount)


async def _heartbeat_schedule(schedule_id: int, lease_token: str) -> None:
    while True:
        await asyncio.sleep(settings.collection_scheduler_heartbeat_seconds)
        if not _renew_schedule_lease(schedule_id, lease_token):
            return


async def process_next_schedule() -> bool:
    with SessionLocal() as db:
        claim = claim_due_schedule(db)
        if claim is None:
            return False
        schedule_id, lease_token = claim
        await execute_claimed_schedule(
            db,
            schedule_id=schedule_id,
            lease_token=lease_token,
        )
        return True


async def scheduler_loop() -> None:
    while True:
        processed = await process_next_schedule()
        if not processed:
            await asyncio.sleep(settings.collection_scheduler_poll_seconds)
