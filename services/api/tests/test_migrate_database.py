from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.source import Source
from scripts.migrate_database import DEFAULT_SOURCES, migration_files, seed_default_sources


MIGRATIONS = Path(__file__).parents[3] / "infra" / "postgres" / "migrations"


def test_fresh_database_sources_match_current_connector_baseline() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        seed_default_sources(session)
        session.commit()
        sources = list(session.scalars(select(Source).order_by(Source.name)))

    assert len(sources) == len(DEFAULT_SOURCES) == 26
    assert {source.connector_type for source in sources} == {
        "manual",
        "tencent_lol",
        "riot_official",
        "x_twitter",
        "weibo",
        "baidu_tieba",
    }
    assert len(
        {
            (source.connector_type, source.external_key)
            for source in sources
            if source.external_key is not None
        }
    ) == 24
    by_key = {
        source.external_key: source for source in sources if source.external_key is not None
    }
    assert by_key["riotphroxzon"].is_official is True
    assert by_key["riotphroxzon"].reliability_score == 1.0
    for key in (
        "loldev",
        "riotphlox",
        "lck",
        "lec",
        "t1lol",
        "geng",
        "g2league",
        "5926660141",
        "5449734852",
    ):
        assert by_key[key].is_official is True
        assert by_key[key].reliability_score == 1.0
    assert by_key["1992350413"].is_official is False
    assert by_key["1992350413"].reliability_score == 0.6
    lpl_source = next(source for source in sources if source.name == "腾讯英雄联盟赛事官网（LPL）")
    assert lpl_source.connector_type == "tencent_lol"
    assert lpl_source.external_key is None
    assert lpl_source.connector_config == {"target": "25"}
    assert lpl_source.is_official is True
    assert lpl_source.reliability_score == 1.0
    assert {
        by_key[key].reliability_score for key in ("2266865584", "2522098777", "2600241232")
    } == {0.6}
    assert {by_key[key].reliability_score for key in ("86124184", "770437943")} == {0.7}


def test_migration_ledger_and_current_compatibility_contract(monkeypatch) -> None:
    monkeypatch.setenv("MIGRATIONS_DIR", str(MIGRATIONS))
    files = migration_files()
    assert files[0].name == "001_initial_schema.sql"
    assert files[-1].name == "073_update_message_taxonomy_v4.sql"

    taxonomy = (MIGRATIONS / "056_add_message_taxonomy_v1.sql").read_text()
    for column in ("products", "message_type", "topics", "classification_version"):
        assert f"ADD COLUMN {column}" in taxonomy
    for stage in ("relevance", "image_ocr", "translation", "message_analysis", "importance"):
        assert f"'{stage}'" in taxonomy

    normalized_defaults = (
        MIGRATIONS / "057_restore_legacy_normalized_defaults.sql"
    ).read_text()
    assert "ALTER COLUMN primary_topic SET DEFAULT 'other'" in normalized_defaults
    assert "ALTER COLUMN secondary_topics SET DEFAULT '[]'::jsonb" in normalized_defaults

    correction_defaults = (
        MIGRATIONS / "058_restore_legacy_correction_defaults.sql"
    ).read_text()
    assert "ALTER COLUMN original_event_ids SET DEFAULT '[]'::json" in correction_defaults

    importance_policy = (
        MIGRATIONS / "059_update_importance_policy_v9.sql"
    ).read_text()
    assert "importance-v9-classification-native" in importance_policy

    community_promotion = (
        MIGRATIONS / "060_add_community_promotion_type.sql"
    ).read_text()
    assert "message-taxonomy-v2" in community_promotion
    assert "importance-v10-community-promotion" in community_promotion

    current_importance = (
        MIGRATIONS / "061_update_importance_policy_v11.sql"
    ).read_text()
    assert "importance-v11-repost-weekly-rotation" in current_importance

    current_taxonomy = (MIGRATIONS / "062_update_message_taxonomy_v3.sql").read_text()
    assert "message-taxonomy-v3" in current_taxonomy
    assert "message-processing-v1.1" in current_taxonomy

    current_taxonomy_v4 = (
        MIGRATIONS / "073_update_message_taxonomy_v4.sql"
    ).read_text()
    assert "message-taxonomy-v4" in current_taxonomy_v4
    assert "'073_update_message_taxonomy_v4'" in current_taxonomy_v4

    event_schema = (MIGRATIONS / "063_replace_event_system_with_v1.sql").read_text()
    assert "DROP TABLE IF EXISTS event_messages" in event_schema
    assert "DROP TABLE IF EXISTS event_review_tasks" in event_schema
    assert "DROP COLUMN IF EXISTS original_event_ids" in event_schema
    assert "CREATE TABLE event_mentions" in event_schema
    assert "normalized_item_revision integer NOT NULL" in event_schema
    assert "impact_snapshot jsonb NOT NULL" in event_schema
    assert "uq_event_mentions_item_index_policy" in event_schema
    assert "event-aggregation-v1" in event_schema

    requested_sources = (MIGRATIONS / "064_add_requested_sources.sql").read_text()
    for external_key in (
        "loldev",
        "riotphlox",
        "lck",
        "lec",
        "t1lol",
        "geng",
        "g2league",
        "5926660141",
        "5449734852",
        "1992350413",
    ):
        assert f"'{external_key}'" in requested_sources
    assert "'腾讯英雄联盟赛事官网（LPL）'" in requested_sources
    assert "'{\"target\": \"25\"}'::json" in requested_sources
    assert "'064_add_requested_sources'" in requested_sources

    unresolved_points = (MIGRATIONS / "065_remove_event_unresolved_points.sql").read_text()
    assert "DROP COLUMN IF EXISTS unresolved_points" in unresolved_points
    assert "'065_remove_event_unresolved_points'" in unresolved_points

    daily_reports = (MIGRATIONS / "066_add_daily_reports.sql").read_text()
    assert "CREATE TABLE daily_reports" in daily_reports
    assert "id serial PRIMARY KEY" in daily_reports
    assert "report_date date NOT NULL UNIQUE" in daily_reports
    assert "CREATE TABLE daily_report_items" in daily_reports
    assert "uq_daily_report_item_position" in daily_reports

    compatibility_cleanup = (
        MIGRATIONS / "067_remove_runtime_compatibility_and_enforce_state.sql"
    ).read_text()
    for column in (
        "aggregation_key",
        "impact_snapshot",
        "product_scope",
        "primary_topic",
        "secondary_topics",
        "subtopic",
        "source_kind",
        "information_stage",
        "ontology_version",
    ):
        assert f"DROP COLUMN IF EXISTS {column}" in compatibility_cleanup
    for table in ("claims", "digests", "digest_revisions"):
        assert f"DROP TABLE IF EXISTS {table}" in compatibility_cleanup

    event_products_index = (MIGRATIONS / "068_add_event_products_index.sql").read_text()
    assert "CREATE INDEX ix_events_products_gin" in event_products_index
    assert "ON events USING gin(products)" in event_products_index
    assert "'068_add_event_products_index'" in event_products_index

    event_run_fencing = (MIGRATIONS / "069_fence_event_runs_by_revision.sql").read_text()
    assert "normalized_item_id, normalized_item_revision" in event_run_fencing
    assert "'069_fence_event_runs_by_revision'" in event_run_fencing

    notification_outbox = (MIGRATIONS / "070_add_notification_outbox.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS notification_outbox" in notification_outbox
    for column in (
        "target",
        "kind",
        "dedupe_key",
        "payload",
        "status",
        "attempts",
        "next_attempt_at",
        "lease_token",
        "lease_expires_at",
    ):
        assert column in notification_outbox
    assert "'070_add_notification_outbox'" in notification_outbox

    pipeline_retry = (MIGRATIONS / "071_add_pipeline_retry_schedule.sql").read_text()
    assert "ADD COLUMN next_attempt_at timestamptz" in pipeline_retry
    assert "ix_pipeline_jobs_next_attempt_at" in pipeline_retry
    assert "'071_add_pipeline_retry_schedule'" in pipeline_retry

    active_pipeline_jobs = (
        MIGRATIONS / "072_include_retry_pending_pipeline_jobs.sql"
    ).read_text()
    assert "DROP INDEX IF EXISTS uq_pipeline_jobs_active_raw_item" in active_pipeline_jobs
    assert "status IN ('queued', 'running')" in active_pipeline_jobs
    assert "status = 'failed' AND next_attempt_at IS NOT NULL" in active_pipeline_jobs
    assert "'072_include_retry_pending_pipeline_jobs'" in active_pipeline_jobs
