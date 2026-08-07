from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.source import Source
from scripts.migrate_database import (
    DEFAULT_SOURCES,
    migration_files,
    seed_default_sources,
)


def test_fresh_database_sources_match_current_connector_baseline() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        seed_default_sources(session)
        session.commit()
        sources = list(session.scalars(select(Source).order_by(Source.name)))

    assert len(sources) == len(DEFAULT_SOURCES) == 15
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
    ) == 14
    by_external_key = {
        source.external_key: source
        for source in sources
        if source.external_key is not None
    }
    assert by_external_key["riotphroxzon"].is_official is True
    assert by_external_key["riotphroxzon"].reliability_score == 1.0
    assert {
        by_external_key[key].reliability_score
        for key in ("2266865584", "2522098777", "2600241232")
    } == {0.6}
    assert {
        by_external_key[key].reliability_score
        for key in ("86124184", "770437943")
    } == {0.7}


def test_migration_ledger_is_contiguous_and_self_records_version(
    monkeypatch,
) -> None:
    migrations = Path(__file__).parents[3] / "infra" / "postgres" / "migrations"
    monkeypatch.setenv("MIGRATIONS_DIR", str(migrations))
    files = migration_files()
    assert files[0].name.startswith("002_")
    assert files[-1].name == "055_update_event_market_reach_policy.sql"


def test_runtime_compatibility_migration_supports_json_and_jsonb_databases() -> None:
    migration = (
        Path(__file__).parents[3]
        / "infra"
        / "postgres"
        / "migrations"
        / "054_remove_runtime_compatibility_layers.sql"
    ).read_text(encoding="utf-8")

    assert "json_array_length(original_event_ids::json)" in migration
