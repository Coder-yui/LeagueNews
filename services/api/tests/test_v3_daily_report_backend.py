import asyncio
from datetime import UTC, date, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.daily_report import DailyReport, DailyReportItem
from app.models.event import Event, EventMention
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.methods import MethodAssembly
from app.orchestration.contracts import ReviewMode, RunMode
from app.orchestration.daily_report import (
    DailyReportBackendV3,
    DailyReportRequest,
    build_daily_report_graph,
)


def _database():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _message(
    db: Session,
    *,
    source_id: int,
    external_id: str,
    score: float,
    published_at: datetime,
) -> NormalizedItem:
    raw = RawItem(
        source_id=source_id,
        external_id=external_id,
        native_title=external_id,
        content_blocks=[{"type": "paragraph", "text": external_id}],
        published_at=published_at,
    )
    db.add(raw)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw.id,
        normalized_title=external_id,
        normalized_text=external_id,
        summary=external_id,
        products=["lol_pc"],
        message_type="game_announcement",
        topics=["gameplay"],
        content_form="original",
        importance_score=score,
        analysis_model="test",
        translated_title=external_id,
        translation_status="not_required",
        publication_status="published",
    )
    db.add(item)
    db.flush()
    return item


def test_daily_report_graph_preserves_v2_rules_and_preview_is_write_free() -> None:
    factory = _database()
    with factory() as db:
        source = Source(name="v3-daily")
        db.add(source)
        db.flush()
        first = _message(
            db,
            source_id=source.id,
            external_id="lower-same-event",
            score=0.7,
            published_at=datetime(2026, 8, 20, 1, tzinfo=UTC),
        )
        second = _message(
            db,
            source_id=source.id,
            external_id="higher-same-event",
            score=0.9,
            published_at=datetime(2026, 8, 20, 2, tzinfo=UTC),
        )
        _message(
            db,
            source_id=source.id,
            external_id="below-threshold",
            score=0.59,
            published_at=datetime(2026, 8, 20, 3, tzinfo=UTC),
        )
        event = Event(
            title="同一事件",
            current_summary="同一事件",
            event_family="gameplay_release",
            products=["lol_pc"],
        )
        db.add(event)
        db.flush()
        db.add_all(
            [
                EventMention(
                    event_id=event.id,
                    normalized_item_id=item.id,
                    normalized_item_revision=item.current_revision,
                    mention_index=index,
                )
                for index, item in enumerate((first, second))
            ]
        )
        db.commit()
        expected_id = second.id

    graph = build_daily_report_graph(
        DailyReportBackendV3(factory, method_assembly=MethodAssembly())
    )
    experiment_request = DailyReportRequest(
        workflow_run_id=8001,
        report_date=date(2026, 8, 20),
        run_mode=RunMode.EXPERIMENT,
        review_mode=ReviewMode.AUTOMATIC,
        batch_id=8,
    )
    preview = asyncio.run(
        graph.ainvoke({"request": experiment_request.model_dump(mode="json")})
    )

    assert preview["outcome"] == "preview_completed"
    assert [item["message_id"] for item in preview["ranked"]["sections"]["lolpc"]] == [
        expected_id
    ]
    with factory() as db:
        assert db.scalar(select(func.count(DailyReport.id))) == 0

    production_request = experiment_request.model_copy(
        update={"workflow_run_id": 20260820, "run_mode": RunMode.PRODUCTION, "batch_id": None}
    )
    published = asyncio.run(
        graph.ainvoke({"request": production_request.model_dump(mode="json")})
    )

    assert published["outcome"] == "published"
    assert published["publication"]["item_count"] == 1
    with factory() as db:
        report = db.scalar(
            select(DailyReport).where(DailyReport.report_date == date(2026, 8, 20))
        )
        assert report is not None
        report_item = db.scalar(
            select(DailyReportItem).where(DailyReportItem.report_id == report.id)
        )
        assert report_item is not None
        assert report_item.normalized_item_id == expected_id
