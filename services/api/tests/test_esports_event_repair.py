from datetime import UTC, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.models.daily_report import DailyReport
from app.models.event import Event, EventAggregationRun, EventMention
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.services.events import add_event_mention, create_event
from scripts.repair_recent_esports_event_aggregation import (
    inspect_selection,
    rollback_selection,
    selection_payload,
)


def _item(
    db: Session,
    *,
    source: Source,
    external_id: str,
    published_at: datetime,
) -> NormalizedItem:
    raw = RawItem(
        source_id=source.id,
        external_id=external_id,
        native_title="BLG 对阵 TES",
        content_blocks=[{"type": "paragraph", "text": "BLG 对阵 TES"}],
        published_at=published_at,
    )
    db.add(raw)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw.id,
        normalized_title="BLG 对阵 TES",
        normalized_text="BLG 对阵 TES",
        summary="BLG 对阵 TES",
        entities=[],
        products=["lol_esports"],
        message_type="esports_announcement",
        topics=["esports_matches"],
        content_form="original",
        importance_score=0.8,
        analysis_model="test",
        analysis_version="test",
        publication_status="published",
    )
    db.add(item)
    db.flush()
    return item


def test_repair_rolls_back_all_current_membership_for_selected_match_messages() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="repair source", connector_type="manual")
        db.add(source)
        db.flush()
        original = _item(
            db,
            source=source,
            external_id="match-original",
            published_at=datetime(2026, 8, 14, 8, tzinfo=UTC),
        )
        selected = _item(
            db,
            source=source,
            external_id="match-selected",
            published_at=datetime(2026, 8, 16, 8, tzinfo=UTC),
        )
        db.commit()

        match_event, _ = create_event(
            db,
            normalized_item_id=original.id,
            mention_index=0,
            event_family="esports_match",
            products=["lol_esports"],
            canonical_anchors={"match_date": "2026-08-16"},
            title="BLG 对阵 TES",
            current_summary="赛前消息。",
            evidence_excerpt="BLG 对阵 TES",
        )
        add_event_mention(
            db,
            event_id=match_event.id,
            normalized_item_id=selected.id,
            mention_index=0,
            relation="reports",
            source_role="unknown",
            materiality="material_update",
            evidence_excerpt="BLG 对阵 TES 赛果",
            current_summary="赛果消息。",
        )
        unrelated_event, _ = create_event(
            db,
            normalized_item_id=selected.id,
            mention_index=1,
            event_family="media_release",
            products=["lol_esports"],
            canonical_anchors={},
            title="比赛节目",
            current_summary="同步发布比赛节目。",
            evidence_excerpt="比赛节目",
        )
        db.add(
            EventAggregationRun(
                normalized_item_id=selected.id,
                normalized_item_revision=selected.current_revision,
                status="completed",
                outcome="applied",
                aggregation_policy_version="event-aggregation-v6-lifecycle-cohesion",
                idempotency_key=(
                    f"{selected.id}:{selected.current_revision}:"
                    "event-aggregation-v6-lifecycle-cohesion"
                ),
            )
        )
        db.add(DailyReport(report_date=datetime(2026, 8, 16).date(), status="published"))
        db.commit()

        selection = inspect_selection(db, limit=1)
        payload = selection_payload(selection, database="lol_daily_intel")

        assert selection.item_ids_newest_first == (selected.id,)
        assert len(selection.mention_ids) == 2
        assert set(selection.event_ids) == {match_event.id, unrelated_event.id}
        assert len(selection.run_ids) == 1
        assert payload["selected_items"] == 1
        assert len(str(payload["selection_token"])) == 64
        assert payload["published_reports_to_regenerate"] == ["2026-08-16"]

        result = rollback_selection(db, selection)
        db.commit()

        assert result == {
            "mentions_removed": 2,
            "runs_removed": 1,
            "events_deleted": 1,
            "events_rebuilt": 1,
        }
        assert db.scalar(select(func.count()).select_from(RawItem)) == 2
        assert db.scalar(select(func.count()).select_from(NormalizedItem)) == 2
        assert db.scalar(select(func.count()).select_from(EventMention)) == 1
        assert db.scalar(select(func.count()).select_from(Event)) == 1
        assert db.get(Event, match_event.id) is not None
        assert db.get(Event, unrelated_event.id) is None
        assert db.scalar(select(func.count()).select_from(EventAggregationRun)) == 0


def test_selection_token_changes_with_item_revision() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="token source", connector_type="manual")
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id="token-match",
            published_at=datetime(2026, 8, 16, 8, tzinfo=UTC),
        )
        db.commit()
        create_event(
            db,
            normalized_item_id=item.id,
            mention_index=0,
            event_family="esports_match",
            products=["lol_esports"],
            canonical_anchors={},
            title="BLG 对阵 TES",
            current_summary="比赛消息。",
            evidence_excerpt="BLG 对阵 TES",
        )

        first = inspect_selection(db, limit=1)
        item.current_revision = 2
        db.commit()
        second = inspect_selection(db, limit=1)

        assert first.token != second.token
