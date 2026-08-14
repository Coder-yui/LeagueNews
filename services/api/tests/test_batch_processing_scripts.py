from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
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
