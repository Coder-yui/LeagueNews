import pytest

from scripts.reprocess_all_raw_items import (
    BatchPreflight,
    _validate_preflight,
)
from scripts.reset_downstream_processing import _validate_target


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
