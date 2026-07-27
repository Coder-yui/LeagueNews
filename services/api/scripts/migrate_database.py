from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base, engine
from app.models.ocr_lab import OCRProfile


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


def migration_files() -> list[Path]:
    directory = Path(os.getenv("MIGRATIONS_DIR", "/migrations"))
    files = sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
    if not files:
        raise RuntimeError(f"No SQL migrations were found in {directory}")
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


def seed_fresh_database(files: list[Path]) -> None:
    Base.metadata.create_all(engine)
    ensure_migration_table()

    with Session(engine) as session:
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
