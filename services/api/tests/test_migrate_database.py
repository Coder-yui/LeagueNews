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
    assert files[0].name.startswith("002_")
    assert files[-1].name == "064_add_requested_sources.sql"

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
