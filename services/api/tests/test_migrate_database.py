from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.source import Source
from scripts.migrate_database import DEFAULT_SOURCES, seed_default_sources


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
