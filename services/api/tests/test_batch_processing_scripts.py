import pytest

from scripts.reprocess_all_raw_items import (
    BatchPreflight,
    _validate_preflight,
)
from scripts.reaggregate_all_events import _validate_preflight as _validate_event_batch
from scripts.reset_downstream_processing import DERIVED_TABLES, _validate_target
from scripts.reset_event_aggregation import _validate_target as _validate_event_reset


def test_reset_covers_every_message_level_projection() -> None:
    assert set(DERIVED_TABLES) == {
        "event_claims",
        "claims",
        "digest_revisions",
        "digests",
        "event_review_tasks",
        "event_aggregation_runs",
        "event_revisions",
        "event_messages",
        "events",
        "normalized_item_media_extractions",
        "normalized_item_revisions",
        "pipeline_jobs",
        "processing_checkpoints",
        "pipeline_corrections",
        "review_tasks",
        "processing_runs",
        "media_extractions",
        "normalized_items",
    }


def test_reset_requires_exact_local_database_and_raw_count() -> None:
    _validate_target(
        database_name="lol_daily_intel",
        raw_item_count=609,
        expected_database="lol_daily_intel",
        expected_raw_items=609,
    )
    with pytest.raises(RuntimeError, match="database"):
        _validate_target(
            database_name="cloud_database",
            raw_item_count=609,
            expected_database="lol_daily_intel",
            expected_raw_items=609,
        )
    with pytest.raises(RuntimeError, match="raw_items=608"):
        _validate_target(
            database_name="lol_daily_intel",
            raw_item_count=608,
            expected_database="lol_daily_intel",
            expected_raw_items=609,
        )


def test_full_batch_requires_clean_derived_state_unless_resuming() -> None:
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


def test_event_reset_requires_exact_local_message_state() -> None:
    arguments = {
        "database_name": "lol_daily_intel",
        "database_host": "localhost",
        "raw_item_count": 737,
        "published_item_count": 64,
        "expected_database": "lol_daily_intel",
        "expected_raw_items": 737,
        "expected_published_items": 64,
    }
    _validate_event_reset(**arguments)
    with pytest.raises(RuntimeError, match="not local"):
        _validate_event_reset(**{**arguments, "database_host": "db.example.com"})
    with pytest.raises(RuntimeError, match="published_items=63"):
        _validate_event_reset(**{**arguments, "published_item_count": 63})


def test_event_batch_requires_empty_event_layer_unless_resuming() -> None:
    clean = {
        "database": "lol_daily_intel",
        "database_host": "127.0.0.1",
        "raw_items": 737,
        "published_items": 64,
        "events": 0,
        "event_messages": 0,
        "event_runs": 0,
        "event_reviews": 0,
        "event_checkpoints": 0,
        "active_pipeline_jobs": 0,
        "llm_configured": True,
    }
    _validate_event_batch(
        clean,
        expected_database="lol_daily_intel",
        expected_raw_items=737,
        expected_published_items=64,
        resume=False,
    )
    interrupted = {**clean, "events": 10, "event_runs": 12}
    with pytest.raises(RuntimeError, match="event-layer data exists"):
        _validate_event_batch(
            interrupted,
            expected_database="lol_daily_intel",
            expected_raw_items=737,
            expected_published_items=64,
            resume=False,
        )
    _validate_event_batch(
        interrupted,
        expected_database="lol_daily_intel",
        expected_raw_items=737,
        expected_published_items=64,
        resume=True,
    )
