import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import scripts.probe_collection_sources as probe_collection_sources
import scripts.reset_event_layer as reset_event_layer
from app.core.database import Base
from app.models.event import Event
from app.models.raw_item import RawItem
from app.models.source import Source
from scripts.bulk_collect_raw_items import _in_time_range, _jittered_delay
from scripts.reprocess_all_raw_items import BatchPreflight, _raw_item_ids, _validate_preflight


def test_bulk_collection_time_range_is_inclusive_and_requires_published_at() -> None:
    since = datetime(2026, 7, 25, tzinfo=UTC)
    until = datetime(2026, 8, 14, 10, tzinfo=UTC)

    assert _in_time_range(since, since=since, until=until)
    assert _in_time_range(until, since=since, until=until)
    assert not _in_time_range(None, since=since, until=until)
    assert not _in_time_range(since - timedelta(microseconds=1), since=since, until=until)
    assert not _in_time_range(until + timedelta(microseconds=1), since=since, until=until)


def test_bulk_collection_delay_without_jitter_is_stable() -> None:
    assert _jittered_delay(30.0, 0.0) == 30.0


def test_full_batch_requires_clean_message_state_unless_resuming() -> None:
    clean = BatchPreflight(
        database="lol_daily_intel",
        raw_items=609,
        normalized_items=0,
        processing_runs=0,
        pipeline_jobs=0,
        llm_configured=True,
        automation_enabled=True,
    )
    _validate_preflight(
        clean,
        expected_database="lol_daily_intel",
        expected_raw_items=609,
        resume=False,
    )

    interrupted = BatchPreflight(
        database="lol_daily_intel",
        raw_items=609,
        normalized_items=17,
        processing_runs=20,
        pipeline_jobs=609,
        llm_configured=True,
        automation_enabled=True,
    )
    with pytest.raises(RuntimeError, match="derived pipeline data"):
        _validate_preflight(
            interrupted,
            expected_database="lol_daily_intel",
            expected_raw_items=609,
            resume=False,
        )
    _validate_preflight(
        interrupted,
        expected_database="lol_daily_intel",
        expected_raw_items=609,
        resume=True,
    )


def test_full_batch_processes_only_latest_raw_revisions() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name="revision-test", connector_type="manual")
        db.add(source)
        db.flush()
        original = RawItem(
            source_id=source.id,
            external_id="message-1",
            revision=1,
            content_blocks=[{"id": "b0001", "type": "paragraph", "text": "old"}],
        )
        db.add(original)
        db.flush()
        revision = RawItem(
            source_id=source.id,
            external_id="message-1",
            revision=2,
            supersedes_raw_item_id=original.id,
            content_blocks=[{"id": "b0001", "type": "paragraph", "text": "new"}],
        )
        independent = RawItem(
            source_id=source.id,
            external_id="message-2",
            revision=1,
            content_blocks=[{"id": "b0001", "type": "paragraph", "text": "current"}],
        )
        db.add_all([revision, independent])
        db.commit()

        assert _raw_item_ids(db) == [revision.id, independent.id]


def _script_session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)


def test_reset_event_layer_clears_events_and_preserves_raw_items(monkeypatch) -> None:
    engine, factory = _script_session_factory()
    with factory() as db:
        source = Source(name="event reset source", connector_type="manual")
        db.add(source)
        db.flush()
        raw = RawItem(
            source_id=source.id,
            content_blocks=[{"type": "paragraph", "text": "source evidence"}],
        )
        db.add_all([raw, Event(title="Event", current_summary="Summary")])
        db.commit()

    monkeypatch.setattr(reset_event_layer, "SessionLocal", factory)
    monkeypatch.setattr(reset_event_layer, "_validate_local_database", lambda: None)
    reset_event_layer.reset_event_layer(apply=True)

    with Session(engine) as db:
        assert db.query(RawItem).count() == 1
        assert db.query(Event).count() == 0


def test_reset_event_layer_rolls_back_when_an_invariant_fails(monkeypatch) -> None:
    engine, factory = _script_session_factory()
    with factory() as db:
        db.add(Event(title="Event", current_summary="Summary"))
        db.commit()

    original_counts = reset_event_layer._counts
    count_calls = 0

    def failing_counts(db):
        nonlocal count_calls
        count_calls += 1
        counts = original_counts(db)
        if count_calls > 1:
            counts["raw_items"] += 1
        return counts

    monkeypatch.setattr(reset_event_layer, "SessionLocal", factory)
    monkeypatch.setattr(reset_event_layer, "_validate_local_database", lambda: None)
    monkeypatch.setattr(reset_event_layer, "_counts", failing_counts)
    with pytest.raises(RuntimeError, match="RawItem count changed"):
        reset_event_layer.reset_event_layer(apply=True)

    with Session(engine) as db:
        assert db.query(Event).count() == 1


def _probe_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        since="2026-08-01T00:00:00+00:00",
        until="2026-08-02T00:00:00+00:00",
        source_delay=0.0,
        source_id=None,
        connector_type=None,
        probe_only=True,
        state_file=tmp_path / "state.json",
        report_file=tmp_path / "report.md",
    )


def test_collection_probe_succeeds_when_every_source_succeeds(tmp_path: Path, monkeypatch) -> None:
    _engine, factory = _script_session_factory()
    with factory() as db:
        db.add(Source(name="working source", connector_type="working"))
        db.commit()

    class WorkingConnector:
        async def collect(self, _request):
            return []

    monkeypatch.setattr(probe_collection_sources, "SessionLocal", factory)
    monkeypatch.setattr(probe_collection_sources, "_source_context", lambda source: source)
    monkeypatch.setattr(probe_collection_sources.connector_registry, "create", lambda _kind: WorkingConnector())
    assert asyncio.run(probe_collection_sources._run(_probe_args(tmp_path))) is True
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert "成功：`1`" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_collection_probe_records_failures_and_returns_false(tmp_path: Path, monkeypatch) -> None:
    _engine, factory = _script_session_factory()
    with factory() as db:
        db.add_all(
            [
                Source(name="working source", connector_type="working"),
                Source(name="failing source", connector_type="failing"),
            ]
        )
        db.commit()

    class WorkingConnector:
        async def collect(self, _request):
            return []

    class FailingConnector:
        async def collect(self, _request):
            raise RuntimeError("unreachable")

    def create(kind: str):
        return WorkingConnector() if kind == "working" else FailingConnector()

    monkeypatch.setattr(probe_collection_sources, "SessionLocal", factory)
    monkeypatch.setattr(probe_collection_sources, "_source_context", lambda source: source)
    monkeypatch.setattr(probe_collection_sources.connector_registry, "create", create)
    assert asyncio.run(probe_collection_sources._run(_probe_args(tmp_path))) is False
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed_with_failures"
    assert state["sources"]["2"]["status"] == "failed"
    assert "失败：`1`" in (tmp_path / "report.md").read_text(encoding="utf-8")
