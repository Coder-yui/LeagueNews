from collections import Counter
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
from scripts.repair_all_esports_event_aggregation import (
    all_esports_item_ids,
    inspect_all_selection,
)
from scripts.repair_recent_esports_event_aggregation import (
    audit_esports_match_events,
    inspect_selection,
    post_repair_audit,
    repair_failure_reason,
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
            canonical_anchors={
                "participants": ["BLG", "TES"],
                "match_date": "2026-08-16",
            },
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
            "events_deleted_invalid_shell": 0,
            "events_rebuilt": 1,
        }
        assert db.scalar(select(func.count()).select_from(RawItem)) == 2
        assert db.scalar(select(func.count()).select_from(NormalizedItem)) == 2
        assert db.scalar(select(func.count()).select_from(EventMention)) == 1
        assert db.scalar(select(func.count()).select_from(Event)) == 1
        assert db.get(Event, match_event.id) is not None
        assert db.get(Event, unrelated_event.id) is None
        assert db.scalar(select(func.count()).select_from(EventAggregationRun)) == 0


def test_repair_deletes_shell_esports_match_without_usable_subject() -> None:
    """An esports_match that cannot rebuild a minimal valid projection after rollback
    (no recognizable match subject) is deleted instead of being left as an empty shell."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="repair shell", connector_type="manual")
        db.add(source)
        db.flush()
        keeper = _item(
            db,
            source=source,
            external_id="shell-keeper",
            published_at=datetime(2026, 8, 14, 8, tzinfo=UTC),
        )
        selected = _item(
            db,
            source=source,
            external_id="shell-selected",
            published_at=datetime(2026, 8, 16, 8, tzinfo=UTC),
        )
        db.commit()

        shell_event, _ = create_event(
            db,
            normalized_item_id=keeper.id,
            mention_index=0,
            event_family="esports_match",
            products=["lol_esports"],
            canonical_anchors={"match_date": "2026-08-16"},
            title="某场比赛",
            current_summary="赛前消息。",
            evidence_excerpt="某场比赛",
        )
        add_event_mention(
            db,
            event_id=shell_event.id,
            normalized_item_id=selected.id,
            mention_index=0,
            relation="reports",
            source_role="unknown",
            materiality="material_update",
            evidence_excerpt="某场比赛赛果",
            current_summary="赛果消息。",
        )
        db.add(
            EventAggregationRun(
                normalized_item_id=selected.id,
                normalized_item_revision=selected.current_revision,
                status="completed",
                outcome="applied",
                aggregation_policy_version="event-aggregation-v12-identity-gate-subject-continuation",
                idempotency_key=(
                    f"{selected.id}:{selected.current_revision}:"
                    "event-aggregation-v12-identity-gate-subject-continuation"
                ),
            )
        )
        db.commit()

        selection = inspect_selection(db, limit=1)
        assert selection.event_ids == (shell_event.id,)

        result = rollback_selection(db, selection)
        db.commit()

        assert result == {
            "mentions_removed": 1,
            "runs_removed": 1,
            "events_deleted": 0,
            "events_deleted_invalid_shell": 1,
            "events_rebuilt": 0,
        }
        assert db.get(Event, shell_event.id) is None


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


# ---------------------------------------------------------------------------
# v13: evidence-identity audit + full-repair selection / post-repair audit
# ---------------------------------------------------------------------------


def _match_item(
    db: Session,
    *,
    source: Source,
    external_id: str,
    published_at: datetime,
    title: str,
) -> NormalizedItem:
    raw = RawItem(
        source_id=source.id,
        external_id=external_id,
        native_title=title,
        content_blocks=[{"type": "paragraph", "text": title}],
        published_at=published_at,
    )
    db.add(raw)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw.id,
        normalized_title=title,
        normalized_text=title,
        summary=title,
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


def _create_match_event(
    db: Session,
    *,
    item: NormalizedItem,
    participants: list[str],
    match_date: str | None,
    title: str,
) -> Event:
    identity: dict[str, object] = {"participants": participants}
    if match_date:
        identity["match_date"] = match_date
    event, _ = create_event(
        db,
        normalized_item_id=item.id,
        mention_index=0,
        event_family="esports_match",
        products=["lol_esports"],
        canonical_anchors=dict(identity),
        title=title,
        current_summary="比赛消息。",
        evidence_excerpt=title,
        structured_fact_changes={"match_identity": dict(identity)},
    )
    return event


def test_audit_flags_false_merge_member_identity_conflict() -> None:
    """I.19: a WBG/IG member inside a JDG/LGD Event is a false merge."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="audit false merge", connector_type="manual")
        db.add(source)
        db.flush()
        jdg_item = _match_item(
            db, source=source, external_id="jdg-lgd", title="JDG 对阵 LGD",
            published_at=datetime(2026, 8, 16, 8, tzinfo=UTC),
        )
        wbg_item = _match_item(
            db, source=source, external_id="wbg-ig", title="WBG 对阵 IG",
            published_at=datetime(2026, 8, 16, 10, tzinfo=UTC),
        )
        db.commit()
        event = _create_match_event(
            db, item=jdg_item, participants=["JDG", "LGD"],
            match_date="2026-08-16", title="JDG 对阵 LGD",
        )
        add_event_mention(
            db,
            event_id=event.id,
            normalized_item_id=wbg_item.id,
            mention_index=0,
            relation="reports",
            source_role="unknown",
            materiality="material_update",
            evidence_excerpt="WBG 对阵 IG",
            current_summary="另一场比赛。",
            structured_fact_changes={
                "match_identity": {"participants": ["WBG", "IG"]}
            },
        )
        db.commit()

        audit = audit_esports_match_events(db)

        assert audit["clean"] is False
        violations = audit["false_merge_violations"]
        assert len(violations) == 1
        violation = violations[0]
        assert violation["event_id"] == event.id
        assert violation["normalized_item_id"] == wbg_item.id
        assert violation["event_participants"] == ["JDG", "LGD"]
        assert violation["member_participants"] == ["WBG", "IG"]
        assert str(violation["reason"]).startswith("participants")

        failure = repair_failure_reason(Counter(["applied", "applied"]), audit)
        assert failure is not None
        assert "post-repair audit failed" in failure


def test_audit_same_participants_different_dates_not_duplicate() -> None:
    """I.20: BLG/TES on 08-14 vs BLG/TES on 08-16 are distinct occurrences."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="audit distinct", connector_type="manual")
        db.add(source)
        db.flush()
        first_item = _match_item(
            db, source=source, external_id="blg-tes-14", title="BLG 对阵 TES",
            published_at=datetime(2026, 8, 14, 8, tzinfo=UTC),
        )
        second_item = _match_item(
            db, source=source, external_id="blg-tes-16", title="BLG 对阵 TES",
            published_at=datetime(2026, 8, 16, 8, tzinfo=UTC),
        )
        db.commit()
        _create_match_event(
            db, item=first_item, participants=["BLG", "TES"],
            match_date="2026-08-14", title="BLG 对阵 TES（14 日）",
        )
        _create_match_event(
            db, item=second_item, participants=["BLG", "TES"],
            match_date="2026-08-16", title="BLG 对阵 TES（16 日）",
        )
        db.commit()

        audit = audit_esports_match_events(db)

        assert audit["strong_same_occurrence_duplicate_groups"] == []
        assert audit["clean"] is True


def test_audit_same_participants_same_date_is_duplicate() -> None:
    """I.21: two Events with the same participants and match_date are duplicates."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="audit duplicate", connector_type="manual")
        db.add(source)
        db.flush()
        first_item = _match_item(
            db, source=source, external_id="blg-tes-a", title="BLG 对阵 TES",
            published_at=datetime(2026, 8, 16, 8, tzinfo=UTC),
        )
        second_item = _match_item(
            db, source=source, external_id="blg-tes-b", title="BLG 对阵 TES 赛果",
            published_at=datetime(2026, 8, 16, 12, tzinfo=UTC),
        )
        db.commit()
        first = _create_match_event(
            db, item=first_item, participants=["BLG", "TES"],
            match_date="2026-08-16", title="BLG 对阵 TES",
        )
        second = _create_match_event(
            db, item=second_item, participants=["BLG", "TES"],
            match_date="2026-08-16", title="BLG 对阵 TES 赛果",
        )
        db.commit()

        audit = audit_esports_match_events(db)

        assert audit["clean"] is False
        groups = audit["strong_same_occurrence_duplicate_groups"]
        assert len(groups) == 1
        assert groups[0]["event_ids"] == [first.id, second.id]
        assert "match_date 一致" in str(groups[0]["reason"])


def test_full_repair_selects_routed_items_without_mentions() -> None:
    """J.22: selection is routing-based; previously ignored messages are included."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="full repair selection", connector_type="manual")
        db.add(source)
        db.flush()
        ignored_match = _match_item(
            db, source=source, external_id="ignored-match", title="WBG 对阵 IG",
            published_at=datetime(2026, 8, 16, 8, tzinfo=UTC),
        )
        raw = RawItem(
            source_id=source.id,
            external_id="gameplay-msg",
            native_title="26.17 版本平衡调整",
            content_blocks=[{"type": "paragraph", "text": "26.17 版本平衡调整"}],
            published_at=datetime(2026, 8, 16, 9, tzinfo=UTC),
        )
        db.add(raw)
        db.flush()
        gameplay_item = NormalizedItem(
            raw_item_id=raw.id,
            normalized_title="26.17 版本平衡调整",
            normalized_text="26.17 版本平衡调整",
            summary="26.17 版本平衡调整",
            entities=[],
            products=["lol_pc"],
            message_type="game_announcement",
            topics=["balance_gameplay"],
            content_form="original",
            importance_score=0.7,
            analysis_model="test",
            analysis_version="test",
            publication_status="published",
        )
        db.add(gameplay_item)
        db.commit()

        selected = all_esports_item_ids(db)

        # The previously ignored esports message (no EventMention at all) is
        # selected; the gameplay message routed outside the esports space is not.
        assert ignored_match.id in selected
        assert gameplay_item.id not in selected


def test_full_repair_rolls_back_all_families_for_selected_items() -> None:
    """Full repair rolls back the WHOLE item, not just esports_match memberships.

    Re-aggregation re-decides every family for the message, so any surviving
    non-match mention (schedule, roster, ...) would be duplicated by the fresh
    run: production evidence showed the same item attached to the same schedule
    event twice after a match-family-only rollback. The selection must therefore
    include every current mention of the routed items.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="full repair all families", connector_type="manual")
        db.add(source)
        db.flush()
        mixed = _item(
            db, source=source, external_id="mixed-routing",
            published_at=datetime(2026, 8, 16, 8, tzinfo=UTC),
        )
        db.commit()

        schedule_event, _created = create_event(
            db,
            normalized_item_id=mixed.id,
            mention_index=0,
            event_family="esports_schedule",
            products=["lol_esports"],
            canonical_anchors={"competition": "LPL"},
            title="LPL 赛程安排",
            current_summary="赛程公告。",
            evidence_excerpt="LPL 赛程安排",
        )
        db.commit()
        schedule_mention = db.scalar(
            select(EventMention).where(EventMention.event_id == schedule_event.id)
        )

        selection = inspect_all_selection(db)

        assert selection.item_ids_newest_first == (mixed.id,)
        assert schedule_mention is not None
        assert schedule_mention.id in selection.mention_ids
        assert schedule_event.id in selection.event_ids


def test_post_repair_audit_scopes_to_reaggregated_items_but_compares_family() -> None:
    """J.23: scoped to touched Events, yet the duplicate comparison stays family-wide."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="post audit scope", connector_type="manual")
        db.add(source)
        db.flush()
        reaggregated = _match_item(
            db, source=source, external_id="blg-tes-new", title="BLG 对阵 TES",
            published_at=datetime(2026, 8, 16, 8, tzinfo=UTC),
        )
        legacy = _match_item(
            db, source=source, external_id="blg-tes-old", title="BLG 对阵 TES",
            published_at=datetime(2026, 8, 16, 6, tzinfo=UTC),
        )
        other = _match_item(
            db, source=source, external_id="jdg-lgd", title="JDG 对阵 LGD",
            published_at=datetime(2026, 8, 16, 7, tzinfo=UTC),
        )
        db.commit()
        _create_match_event(
            db, item=reaggregated, participants=["BLG", "TES"],
            match_date="2026-08-16", title="BLG 对阵 TES",
        )
        legacy_event = _create_match_event(
            db, item=legacy, participants=["BLG", "TES"],
            match_date="2026-08-16", title="BLG 对阵 TES（重复）",
        )
        _create_match_event(
            db, item=other, participants=["JDG", "LGD"],
            match_date="2026-08-16", title="JDG 对阵 LGD",
        )
        db.commit()

        audit = post_repair_audit(db, reaggregated_item_ids={reaggregated.id})

        # Only the reaggregated item's Event is in scope ...
        assert audit["audited_events"] == 1
        # ... but its strong same-occurrence duplicate against the untouched
        # legacy Event is still caught.
        groups = audit["strong_same_occurrence_duplicate_groups"]
        assert len(groups) == 1
        assert legacy_event.id in groups[0]["event_ids"]
        assert audit["clean"] is False


def test_repair_failure_reason_gates_on_outcomes_and_clean_audit() -> None:
    """J.24: any failed item or unclean post-repair audit fails the repair."""
    assert (
        repair_failure_reason(Counter(["applied"]), {"clean": True}) is None
    )
    failure = repair_failure_reason(Counter(["applied", "failed"]), {"clean": True})
    assert failure is not None
    assert "reaggregation failures" in failure
    failure = repair_failure_reason(
        Counter(["applied"]), {"clean": False, "invalid_events": [{"event_id": 9}]}
    )
    assert failure is not None
    assert "post-repair audit failed" in failure
    assert "9" in failure
