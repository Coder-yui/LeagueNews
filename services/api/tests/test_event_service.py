from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.models.event import Event, EventMention, EventRevision
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.services.events import add_event_mention, create_event
from app.services.event_metrics import refresh_event_metrics


def _add_item(
    db: Session,
    *,
    source: Source,
    external_id: str,
    published_at: datetime,
    title: str,
    domain_score: float = 0.5,
    profile: str = "gameplay_announcement",
    content_form: str = "original",
    has_domain_score: bool = True,
) -> NormalizedItem:
    raw = RawItem(
        source_id=source.id,
        external_id=external_id,
        native_title=title,
        canonical_url=f"https://example.com/{external_id}",
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
        products=["lol_pc"],
        message_type="game_announcement",
        topics=["balance_gameplay"],
        content_form=content_form,
        importance_score=round(domain_score - (0.08 if content_form == "repost" else 0), 4),
        importance_calculation=(
            {
                "importance_profile": profile,
                "profile_score": domain_score,
                "final_score": round(
                    domain_score - (0.08 if content_form == "repost" else 0), 4
                ),
            }
            if has_domain_score
            else {}
        ),
        target_language="zh-CN",
        translated_title=title,
        translated_text=title,
        translated_content_blocks=[{"type": "paragraph", "text": title}],
        translation_status="not_required",
        analysis_model="test",
        analysis_version="test",
    )
    db.add(item)
    db.flush()
    return item


def test_events_and_messages_are_many_to_many_and_mentions_are_idempotent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Event source", reliability_score=0.8)
        db.add(source)
        db.flush()
        now = datetime(2026, 8, 11, 8, tzinfo=UTC)
        composite = _add_item(
            db,
            source=source,
            external_id="composite",
            published_at=now,
            title="版本公告包含平衡和活动",
        )
        followup = _add_item(
            db,
            source=source,
            external_id="followup",
            published_at=now + timedelta(hours=2),
            title="平衡改动后续",
        )
        db.commit()

        balance, balance_created = create_event(
            db,
            normalized_item_id=composite.id,
            mention_index=0,
            event_family="gameplay_balance",
            products=["lol_pc"],
            canonical_anchors={"patch_version": "26.17"},
            title="26.17 版本平衡调整",
            current_summary="版本平衡调整已公布。",
            evidence_excerpt="平衡调整",
        )
        activity, activity_created = create_event(
            db,
            normalized_item_id=composite.id,
            mention_index=1,
            event_family="player_activity",
            products=["lol_pc"],
            canonical_anchors={"activity_name": "星界活动"},
            title="星界活动",
            current_summary="星界活动已公布。",
            evidence_excerpt="活动内容",
        )

        assert balance_created is True
        assert activity_created is True
        assert balance.id != activity.id
        assert db.scalar(select(func.count(EventMention.id))) == 2

        updated, added = add_event_mention(
            db,
            event_id=balance.id,
            normalized_item_id=followup.id,
            mention_index=0,
            relation="supports",
            source_role="known_leaker",
            materiality="material_update",
            evidence_excerpt="补充了具体数值",
            current_summary="版本平衡调整补充了具体数值。",
            latest_development="新增具体数值",
        )
        assert added is True
        assert updated.message_count_total == 2
        assert updated.current_revision == 2
        assert updated.current_summary == "版本平衡调整补充了具体数值。"

        repeated, repeated_added = add_event_mention(
            db,
            event_id=balance.id,
            normalized_item_id=followup.id,
            mention_index=0,
            relation="supports",
            source_role="known_leaker",
            materiality="material_update",
            current_summary="不应被重复应用",
        )
        assert repeated_added is False
        assert repeated.current_revision == 2
        assert repeated.message_count_total == 2
        assert db.scalar(select(func.count(EventMention.id))) == 3
        assert db.scalar(select(func.count(EventRevision.id))) == 3

        same_event, duplicate_created = create_event(
            db,
            normalized_item_id=composite.id,
            mention_index=0,
            event_family="gameplay_balance",
            products=["lol_pc"],
            canonical_anchors={"patch_version": "26.17"},
            title="不应创建的新事件",
            current_summary="不应创建。",
        )
        assert duplicate_created is False
        assert same_event.id == balance.id
        assert db.scalar(select(func.count(Event.id))) == 2


def test_event_times_and_state_only_advance_on_material_updates() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Timeline source", is_official=True)
        db.add(source)
        db.flush()
        initial_time = datetime(2026, 8, 11, 8, tzinfo=UTC)
        initial = _add_item(
            db,
            source=source,
            external_id="initial",
            published_at=initial_time,
            title="初始爆料",
        )
        discussion = _add_item(
            db,
            source=source,
            external_id="discussion",
            published_at=initial_time + timedelta(hours=3),
            title="普通讨论",
        )
        confirmation = _add_item(
            db,
            source=source,
            external_id="confirmation",
            published_at=initial_time + timedelta(hours=5),
            title="正式确认",
        )
        db.commit()

        event, _ = create_event(
            db,
            normalized_item_id=initial.id,
            mention_index=0,
            event_family="gameplay_release",
            products=["lol_pc"],
            canonical_anchors={"champion": "new-champion"},
            title="新英雄发布",
            current_summary="新英雄尚未确认。",
            lifecycle_status="unconfirmed",
        )
        original_material_time = event.last_material_update_at

        event, added = add_event_mention(
            db,
            event_id=event.id,
            normalized_item_id=discussion.id,
            mention_index=0,
            relation="mentions",
            source_role="ordinary_account",
            materiality="context_only",
        )
        assert added is True
        assert event.current_revision == 1
        assert event.last_material_update_at == original_material_time
        assert event.last_seen_at.replace(tzinfo=UTC) == discussion.raw_item.published_at.replace(
            tzinfo=UTC
        )

        event, added = add_event_mention(
            db,
            event_id=event.id,
            normalized_item_id=confirmation.id,
            mention_index=0,
            relation="confirms",
            source_role="responsible_official",
            materiality="material_update",
            lifecycle_status="confirmed",
            current_summary="新英雄已经正式确认。",
            latest_development="官方确认",
        )
        assert added is True
        assert event.lifecycle_status == "confirmed"
        assert event.current_revision == 2
        assert (
            event.last_material_update_at.replace(tzinfo=UTC)
            == confirmation.raw_item.published_at.replace(tzinfo=UTC)
        )
        assert event.latest_update_message_id == confirmation.id
        assert event.message_count_total == 3


def test_new_normalized_item_revision_can_be_aggregated_once() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Revision source", reliability_score=0.8)
        db.add(source)
        db.flush()
        item = _add_item(
            db,
            source=source,
            external_id="revised-message",
            published_at=datetime(2026, 8, 11, 8, tzinfo=UTC),
            title="首次发布",
            domain_score=0.86,
            profile="worlds_key",
            content_form="repost",
        )
        db.commit()

        event, created = create_event(
            db,
            normalized_item_id=item.id,
            mention_index=0,
            event_family="gameplay_balance",
            products=["lol_pc"],
            canonical_anchors={"patch_version": "26.17"},
            title="26.17 版本平衡调整",
            current_summary="首次发布。",
        )
        assert created is True
        assert item.importance_score == 0.78
        refresh_event_metrics(db, {event.id})
        assert event.importance_score == 0.78

        item.current_revision = 2
        item.importance_calculation = {
            "importance_profile": "esports_regular",
            "profile_score": 0.6,
        }
        item.importance_score = 0.6
        db.commit()
        event, added = add_event_mention(
            db,
            event_id=event.id,
            normalized_item_id=item.id,
            mention_index=0,
            relation="corrects",
            source_role="independent_media",
            materiality="material_update",
            current_summary="第二修订更正了数值。",
            latest_development="消息第二修订",
        )
        assert added is True
        refresh_event_metrics(db, {event.id})
        assert event.current_revision == 2
        assert event.message_count_total == 1
        assert event.importance_score == 0.6

        repeated, repeated_added = add_event_mention(
            db,
            event_id=event.id,
            normalized_item_id=item.id,
            mention_index=0,
            relation="corrects",
            source_role="independent_media",
            materiality="material_update",
            current_summary="不应重复应用。",
        )
        assert repeated_added is False
        assert repeated.current_revision == 2
        mentions = db.scalars(
            select(EventMention).order_by(EventMention.normalized_item_revision)
        ).all()
        assert [mention.normalized_item_revision for mention in mentions] == [1, 2]


def test_revision_correction_recalculates_lifecycle_from_current_evidence() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Correction official", is_official=True, reliability_score=1)
        db.add(source)
        db.flush()
        item = _add_item(
            db,
            source=source,
            external_id="correction",
            published_at=datetime(2026, 8, 11, 8, tzinfo=UTC),
            title="官方确认",
        )
        db.commit()

        event, _ = create_event(
            db,
            normalized_item_id=item.id,
            mention_index=0,
            event_family="gameplay_release",
            products=["lol_pc"],
            canonical_anchors={"release": "new-feature"},
            title="新功能发布",
            current_summary="官方确认新功能发布。",
            relation="confirms",
            source_role="responsible_official",
            independence_group=f"source:{source.id}",
        )
        refresh_event_metrics(db, {event.id})
        assert event.lifecycle_status == "confirmed"

        item.current_revision = 2
        item.summary = "修订后不再确认。"
        item.translated_title = "修订后不再确认"
        db.commit()
        corrected, added = add_event_mention(
            db,
            event_id=event.id,
            normalized_item_id=item.id,
            mention_index=0,
            relation="mentions",
            source_role="responsible_official",
            materiality="material_update",
            current_summary="修订后不再确认。",
            latest_development="更正此前表述",
        )
        assert added is True
        refresh_event_metrics(db, {event.id})

        assert corrected.lifecycle_status == "developing"
        assert corrected.credibility_level == "unverified"
        assert corrected.official_source_count == 0
        assert corrected.message_count_total == 1
        assert corrected.primary_source_message_id is None
        mentions = db.scalars(
            select(EventMention).where(EventMention.event_id == event.id)
        ).all()
        assert {mention.normalized_item_revision for mention in mentions} == {1, 2}


def test_latest_development_tracks_newest_evidence_in_processing_order() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Match live source", reliability_score=0.8)
        db.add(source)
        db.flush()
        base = datetime(2026, 8, 16, 10, tzinfo=UTC)
        first = _add_item(
            db,
            source=source,
            external_id="match-1-0",
            published_at=base,
            title="BLG 1:0 TES",
        )
        second = _add_item(
            db,
            source=source,
            external_id="match-1-1",
            published_at=base + timedelta(hours=1),
            title="BLG 1:1 TES",
        )
        final = _add_item(
            db,
            source=source,
            external_id="match-2-1",
            published_at=base + timedelta(hours=2),
            title="BLG 2:1 TES，比赛结束",
        )
        db.commit()

        event, _ = create_event(
            db,
            normalized_item_id=first.id,
            mention_index=0,
            event_family="esports_match",
            products=["lol_esports"],
            canonical_anchors={"participants": ["BLG", "TES"]},
            title="BLG 对阵 TES",
            current_summary="BLG 1:0 TES。",
            latest_development="BLG 1:0 TES",
        )
        event, _ = add_event_mention(
            db,
            event_id=event.id,
            normalized_item_id=second.id,
            mention_index=0,
            relation="supports",
            source_role="independent_media",
            materiality="material_update",
            latest_development="BLG 1:1 TES",
        )
        event, _ = add_event_mention(
            db,
            event_id=event.id,
            normalized_item_id=final.id,
            mention_index=0,
            relation="supports",
            source_role="independent_media",
            materiality="material_update",
            latest_development="BLG 2:1 TES，比赛结束",
            lifecycle_status="confirmed",
        )
        refresh_event_metrics(db, {event.id})

        assert event.latest_development == "BLG 2:1 TES，比赛结束"
        assert event.latest_update_message_id == final.id
        assert event.last_material_update_at == base + timedelta(hours=2)


def test_latest_development_not_regressed_by_late_reprocessed_older_message() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Match reorder source", reliability_score=0.8)
        db.add(source)
        db.flush()
        base = datetime(2026, 8, 16, 10, tzinfo=UTC)
        first = _add_item(
            db,
            source=source,
            external_id="reorder-1-0",
            published_at=base,
            title="BLG 1:0 TES",
        )
        second = _add_item(
            db,
            source=source,
            external_id="reorder-1-1",
            published_at=base + timedelta(hours=1),
            title="BLG 1:1 TES",
        )
        final = _add_item(
            db,
            source=source,
            external_id="reorder-2-1",
            published_at=base + timedelta(hours=2),
            title="BLG 2:1 TES，比赛结束",
        )
        db.commit()

        event, _ = create_event(
            db,
            normalized_item_id=first.id,
            mention_index=0,
            event_family="esports_match",
            products=["lol_esports"],
            canonical_anchors={"participants": ["BLG", "TES"]},
            title="BLG 对阵 TES",
            current_summary="BLG 1:0 TES。",
            latest_development="BLG 1:0 TES",
        )
        # The newest evidence is processed first.
        event, _ = add_event_mention(
            db,
            event_id=event.id,
            normalized_item_id=final.id,
            mention_index=0,
            relation="supports",
            source_role="independent_media",
            materiality="material_update",
            latest_development="BLG 2:1 TES，比赛结束",
            lifecycle_status="confirmed",
        )
        assert event.latest_development == "BLG 2:1 TES，比赛结束"

        # An earlier message is (re)processed afterwards: it gets a higher
        # revision number but an older evidence time and must not win.
        event, _ = add_event_mention(
            db,
            event_id=event.id,
            normalized_item_id=second.id,
            mention_index=0,
            relation="supports",
            source_role="independent_media",
            materiality="material_update",
            latest_development="BLG 1:1 TES",
            lifecycle_status="developing",
        )
        assert event.latest_development == "BLG 2:1 TES，比赛结束"
        assert event.lifecycle_status == "confirmed"
        assert event.latest_update_message_id == final.id

        refresh_event_metrics(db, {event.id})
        # The projection replay selects by evidence time, never by revision:
        # the older message wrote the newest revision but must not win.
        assert event.latest_development == "BLG 2:1 TES，比赛结束"
        assert event.latest_update_message_id == final.id
        assert event.last_material_update_at == base + timedelta(hours=2)
