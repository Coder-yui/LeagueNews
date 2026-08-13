from datetime import UTC, date, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.models.daily_report import DailyReport, DailyReportItem
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.services.daily_report_scheduler import (
    due_report_date,
    generate_due_daily_report,
    scheduled_generation_at,
)


def test_daily_report_becomes_due_at_shanghai_noon() -> None:
    report_date = date(2026, 8, 13)

    assert scheduled_generation_at(report_date).isoformat() == "2026-08-13T12:00:00+08:00"
    assert due_report_date(datetime(2026, 8, 13, 3, 59, 59, tzinfo=UTC)) is None
    assert due_report_date(datetime(2026, 8, 13, 4, 0, tzinfo=UTC)) == report_date


def test_due_daily_report_is_generated_once_after_noon() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Scheduled daily report source")
        db.add(source)
        db.flush()
        raw = RawItem(
            source_id=source.id,
            external_id="scheduled-daily-report",
            native_title="午间日报消息",
            content_blocks=[{"type": "paragraph", "text": "午间日报消息"}],
            published_at=datetime(2026, 8, 13, 1, 0, tzinfo=UTC),
        )
        db.add(raw)
        db.flush()
        db.add(
            NormalizedItem(
                raw_item_id=raw.id,
                normalized_title="午间日报消息",
                normalized_text="午间日报消息",
                summary="摘要",
                products=["lol_pc"],
                message_type="game_announcement",
                topics=["gameplay"],
                content_form="original",
                importance_score=0.8,
                analysis_model="test",
                translation_status="not_required",
            )
        )
        db.commit()

        assert (
            generate_due_daily_report(
                db,
                now=datetime(2026, 8, 13, 3, 59, tzinfo=UTC),
            )
            is None
        )
        report = generate_due_daily_report(
            db,
            now=datetime(2026, 8, 13, 4, 0, tzinfo=UTC),
        )
        repeated = generate_due_daily_report(
            db,
            now=datetime(2026, 8, 13, 4, 30, tzinfo=UTC),
        )

        assert report is not None
        assert report.report_date == date(2026, 8, 13)
        assert repeated is None
        assert db.scalar(select(DailyReport).where(DailyReport.report_date == report.report_date))
        assert db.scalar(select(DailyReportItem).where(DailyReportItem.report_id == report.id))


def test_scheduler_does_not_create_an_empty_report() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        report = generate_due_daily_report(
            db,
            now=datetime(2026, 8, 13, 4, 0, tzinfo=UTC),
        )

        assert report is None
        assert db.scalar(select(DailyReport)) is None


def test_scheduler_does_not_republish_a_withdrawn_report() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Withdrawn scheduled report source")
        db.add(source)
        db.flush()
        raw = RawItem(
            source_id=source.id,
            external_id="withdrawn-scheduled-report",
            native_title="已退回日报消息",
            content_blocks=[{"type": "paragraph", "text": "已退回日报消息"}],
            published_at=datetime(2026, 8, 13, 1, 0, tzinfo=UTC),
        )
        db.add(raw)
        db.flush()
        db.add(
            NormalizedItem(
                raw_item_id=raw.id,
                normalized_title="已退回日报消息",
                normalized_text="已退回日报消息",
                summary="摘要",
                products=["lol_pc"],
                message_type="game_announcement",
                topics=["gameplay"],
                content_form="original",
                importance_score=0.8,
                analysis_model="test",
                translation_status="not_required",
            )
        )
        report = DailyReport(
            report_date=date(2026, 8, 13),
            status="withdrawn",
            updated_at=datetime(2026, 8, 13, 2, 0, tzinfo=UTC),
        )
        db.add(report)
        db.commit()

        generated = generate_due_daily_report(
            db,
            now=datetime(2026, 8, 13, 4, 30, tzinfo=UTC),
        )

        assert generated is None
        assert db.get(DailyReport, report.id).status == "withdrawn"
