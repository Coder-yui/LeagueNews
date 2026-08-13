from datetime import UTC, date, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.api.routes.daily_reports import get_daily_report
from app.core.database import Base
from app.models.daily_report import DailyReport, DailyReportItem
from app.models.event import Event, EventMention
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.services.daily_reports import (
    DailyReportCandidate,
    daily_report_section,
    daily_report_window,
    generate_daily_report,
    select_daily_sections,
)


def candidate(
    message_id: int,
    score: float,
    *,
    products: tuple[str, ...] = ("lol_pc",),
    content_form: str = "original",
    event_ids: tuple[int, ...] = (),
    published_at: datetime | None = None,
) -> DailyReportCandidate:
    return DailyReportCandidate(
        message_id=message_id,
        importance_score=score,
        published_at=published_at or datetime(2026, 8, 13, tzinfo=UTC),
        content_form=content_form,
        products=products,
        event_ids=event_ids,
    )


def test_daily_report_filters_original_and_importance_threshold() -> None:
    sections = select_daily_sections(
        [candidate(59, 0.59), candidate(60, 0.60), candidate(61, 0.61), candidate(90, 0.9, content_form="repost")]
    )
    assert [item.message_id for item in sections["lolpc"]] == [61, 60]


def test_daily_report_deduplicates_events_but_keeps_null_events() -> None:
    sections = select_daily_sections(
        [
            candidate(1, 0.70, event_ids=(1,)),
            candidate(2, 0.90, event_ids=(1,)),
            candidate(3, 0.80, event_ids=(2,)),
            candidate(4, 0.85),
            candidate(5, 0.84),
        ]
    )
    assert [item.message_id for item in sections["lolpc"]] == [2, 4, 5, 3]


def test_daily_report_deduplicates_before_top_n() -> None:
    sections = select_daily_sections(
        [
            candidate(1, 1.00, event_ids=(1,)),
            candidate(2, 0.99, event_ids=(1,)),
            candidate(3, 0.98),
            candidate(4, 0.97),
            candidate(5, 0.96),
            candidate(6, 0.95),
            candidate(7, 0.94),
        ]
    )
    assert [item.message_id for item in sections["lolpc"]] == [1, 3, 4, 5, 6]


def test_daily_report_limits_each_section_and_assigns_multi_product_once() -> None:
    assert daily_report_section(("lol_pc", "tft")) == "lolpc"
    assert daily_report_section(("lol_esports", "lol_pc")) == "esports"
    assert daily_report_section(("other_lol_product",)) == "other"
    sections = select_daily_sections(
        [
            candidate(1, 0.95, products=("lol_pc", "tft")),
            *[candidate(index, 0.9, products=("lol_esports",)) for index in range(2, 5)],
            *[candidate(index, 0.9, products=("tft",)) for index in range(5, 9)],
            *[candidate(index, 0.9, products=("other_lol_product",)) for index in range(9, 13)],
        ]
    )
    assert len(sections["esports"]) == 3
    assert len(sections["tft"]) == 3
    assert len(sections["other"]) == 3
    assert sum(item.message_id == 1 for items in sections.values() for item in items) == 1


def test_daily_report_window_uses_shanghai_calendar_boundaries() -> None:
    start, end = daily_report_window(date(2026, 8, 13))
    assert start == datetime(2026, 8, 12, 16, tzinfo=UTC)
    assert end == datetime(2026, 8, 13, 16, tzinfo=UTC)


def test_daily_report_generation_is_replaceable_for_the_same_date() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Daily report source")
        db.add(source)
        db.flush()
        raw = RawItem(
            source_id=source.id,
            external_id="daily-report-1",
            native_title="日报消息",
            content_blocks=[{"type": "paragraph", "text": "日报消息"}],
            published_at=datetime(2026, 8, 12, 16, 0, tzinfo=UTC),
        )
        db.add(raw)
        db.flush()
        item = NormalizedItem(
            raw_item_id=raw.id,
            normalized_title="日报消息",
            normalized_text="日报消息",
            summary="摘要",
            products=["lol_pc"],
            message_type="game_announcement",
            topics=["gameplay"],
            content_form="original",
            importance_score=0.60,
            analysis_model="test",
            translated_title="日报消息",
            translation_status="not_required",
        )
        db.add(item)
        db.flush()
        next_day_raw = RawItem(
            source_id=source.id,
            external_id="daily-report-next-day",
            native_title="次日日报消息",
            content_blocks=[{"type": "paragraph", "text": "次日日报消息"}],
            published_at=datetime(2026, 8, 13, 16, 0, tzinfo=UTC),
        )
        db.add(next_day_raw)
        db.flush()
        db.add(
            NormalizedItem(
                raw_item_id=next_day_raw.id,
                normalized_title="次日日报消息",
                normalized_text="次日日报消息",
                summary="次日摘要",
                products=["lol_pc"],
                message_type="game_announcement",
                topics=["gameplay"],
                content_form="original",
                importance_score=0.99,
                analysis_model="test",
                translated_title="次日日报消息",
                translation_status="not_required",
            )
        )
        event = Event(
            title="日报事件",
            current_summary="摘要",
            event_family="gameplay_release",
            products=["lol_pc"],
        )
        db.add(event)
        db.flush()
        db.add(
            EventMention(
                event_id=event.id,
                normalized_item_id=item.id,
                mention_index=0,
                normalized_item_revision=1,
            )
        )
        db.commit()

        first = generate_daily_report(db, date(2026, 8, 13))
        db.commit()
        first_id = first.id
        first_items = list(
            db.scalars(select(DailyReportItem).where(DailyReportItem.report_id == first.id))
        )
        payload = get_daily_report(date(2026, 8, 13), db)
        second = generate_daily_report(db, date(2026, 8, 13))
        db.commit()

        assert second.id == first_id
        assert db.scalar(select(DailyReport).where(DailyReport.report_date == date(2026, 8, 13))).id == first_id
        assert len(first_items) == 1
        assert [message["id"] for message in payload["sections"]["lolpc"]] == [item.id]
        assert db.scalar(select(DailyReportItem).where(DailyReportItem.report_id == first_id)).normalized_item_id == item.id
