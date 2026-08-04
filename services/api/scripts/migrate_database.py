from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base, engine
from app.models.ocr_lab import OCRProfile
from app.models.source import Source


DEFAULT_OCR_PROFILE = {
    "scale": 2,
    "grayscale": False,
    "contrast": 1,
    "sharpness": 1,
    "text_score": None,
    "box_thresh": None,
    "unclip_ratio": 1.2,
    "use_cls": True,
    "divider_x_ratio": None,
    "line_brightness": 105,
    "line_coverage": 0.82,
}

DEFAULT_SOURCES = (
    {
        "name": "手动导入",
        "connector_type": "manual",
        "external_key": None,
        "base_url": None,
        "connector_config": {},
    },
    {
        "name": "腾讯英雄联盟官方网站",
        "connector_type": "tencent_lol",
        "external_key": "lol.qq.com",
        "base_url": "https://lol.qq.com/",
        "connector_config": {"target": "24"},
    },
    {
        "name": "Riot Games Official",
        "connector_type": "riot_official",
        "external_key": "leagueoflegends.com",
        "base_url": "https://www.leagueoflegends.com/en-us/news/",
        "connector_config": {},
    },
    {
        "name": "Matt Leung-Harrison (@RiotPhroxzon)",
        "connector_type": "x_twitter",
        "external_key": "riotphroxzon",
        "base_url": "https://x.com/RiotPhroxzon",
        "connector_config": {},
    },
    {
        "name": "LoL Esports (@lolesports)",
        "connector_type": "x_twitter",
        "external_key": "lolesports",
        "base_url": "https://x.com/lolesports",
        "connector_config": {},
    },
    {
        "name": "Spideraxe (@Spideraxe30)",
        "connector_type": "x_twitter",
        "external_key": "spideraxe30",
        "base_url": "https://x.com/Spideraxe30",
        "connector_config": {},
    },
    {
        "name": "SkinSpotlights (@SkinSpotlights)",
        "connector_type": "x_twitter",
        "external_key": "skinspotlights",
        "base_url": "https://x.com/SkinSpotlights",
        "connector_config": {},
    },
    {
        "name": "League of Legends (@LeagueofLegends)",
        "connector_type": "x_twitter",
        "external_key": "leagueoflegends",
        "base_url": "https://x.com/LeagueofLegends",
        "connector_config": {},
    },
    {
        "name": "英雄联盟赛事",
        "connector_type": "weibo",
        "external_key": "5756404150",
        "base_url": "https://weibo.com/u/5756404150",
        "connector_config": {"include_reposts": True},
    },
    {
        "name": "英雄联盟",
        "connector_type": "weibo",
        "external_key": "5720474518",
        "base_url": "https://weibo.com/u/5720474518",
        "connector_config": {"include_reposts": True},
    },
    {
        "name": "恋恋红茶_244",
        "connector_type": "weibo",
        "external_key": "2266865584",
        "base_url": "https://weibo.com/u/2266865584",
        "connector_config": {"include_reposts": True},
    },
    {
        "name": "召唤师Park",
        "connector_type": "weibo",
        "external_key": "2522098777",
        "base_url": "https://weibo.com/u/2522098777",
        "connector_config": {"include_reposts": True},
    },
    {
        "name": "_尧阿尧y_",
        "connector_type": "weibo",
        "external_key": "2600241232",
        "base_url": "https://weibo.com/u/2600241232",
        "connector_config": {"include_reposts": True},
    },
    {
        "name": "lol半价吧 · 小老鼠小伟",
        "connector_type": "baidu_tieba",
        "external_key": "86124184",
        "base_url": (
            "https://tieba.baidu.com/home/main?"
            "id=tb.1.1d0b2530.0ZbI4ZqXy-dJplytHVhuQQ"
        ),
        "connector_config": {
            "forum_name": "lol半价",
            "max_thread_pages": 5,
            "max_post_pages": 100,
        },
    },
    {
        "name": "lol半价吧 · 凤舞天_惊鸿恋",
        "connector_type": "baidu_tieba",
        "external_key": "770437943",
        "base_url": (
            "https://tieba.baidu.com/home/main?"
            "id=tb.1.dda57dd7.f1PcHOitsXB66qcRaCI4kQ"
        ),
        "connector_config": {
            "forum_name": "lol半价",
            "max_thread_pages": 5,
            "max_post_pages": 100,
        },
    },
)


def migration_files() -> list[Path]:
    directory = Path(os.getenv("MIGRATIONS_DIR", "/migrations"))
    files = sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
    if not files:
        raise RuntimeError(f"No SQL migrations were found in {directory}")
    numbers = [int(file.name.split("_", 1)[0]) for file in files]
    expected = list(range(numbers[0], numbers[-1] + 1))
    if numbers != expected or len(numbers) != len(set(numbers)):
        raise RuntimeError("SQL migration numbers must be unique and contiguous")
    for file in files:
        if file.stem not in file.read_text(encoding="utf-8"):
            raise RuntimeError(
                f"{file.name} does not record its exact migration version"
            )
    return files


def ensure_migration_table() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version varchar(100) PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
        )


def seed_default_sources(session: Session) -> None:
    session.add_all(Source(**source) for source in DEFAULT_SOURCES)


def seed_fresh_database(files: list[Path]) -> None:
    Base.metadata.create_all(engine)
    ensure_migration_table()

    with Session(engine) as session:
        seed_default_sources(session)
        session.execute(
            text(
                """
                INSERT INTO schema_migrations(version)
                VALUES (:version)
                ON CONFLICT (version) DO NOTHING
                """
            ),
            [{"version": file.stem} for file in files],
        )
        if session.query(OCRProfile).filter(OCRProfile.is_active.is_(True)).first() is None:
            session.add(
                OCRProfile(
                    name="production-2026-07-25",
                    parameters=DEFAULT_OCR_PROFILE,
                    is_active=True,
                )
            )
        session.commit()

    print(f"Initialized a fresh database at the current schema ({len(files)} versions).")


def apply_pending_migrations(files: list[Path]) -> None:
    ensure_migration_table()
    with engine.connect() as connection:
        applied = set(connection.execute(text("SELECT version FROM schema_migrations")).scalars())

    pending = [file for file in files if file.stem not in applied]
    if not pending:
        print("Database schema is current.")
        return

    raw_connection = engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        for file in pending:
            print(f"Applying {file.name}...")
            cursor.execute(file.read_text(encoding="utf-8"))
        raw_connection.commit()
    except Exception:
        raw_connection.rollback()
        raise
    finally:
        raw_connection.close()

    print(f"Applied {len(pending)} migration(s).")


def main() -> None:
    files = migration_files()
    existing_tables = set(inspect(engine).get_table_names())
    if "sources" not in existing_tables and "raw_items" not in existing_tables:
        seed_fresh_database(files)
        return
    apply_pending_migrations(files)


if __name__ == "__main__":
    main()
