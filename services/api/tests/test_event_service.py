from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.models.event import Event, EventMention, EventRevision
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.domain.importance import score_importance_profile
from app.services.events import add_event_mention as _add_event_mention
from app.services.events import create_event as _create_event


def _snapshot(profile: str = "gameplay_announcement", **overrides: object) -> dict[str, object]:
    features = {
        "scale": "standard",
        "competition_region": "none",
        "prominence": "normal",
        "skin_tier": "none",
        "is_bulk_update": False,
        **overrides,
    }
    result = score_importance_profile(profile, features)
    return {
        "policy_version": "importance-v11-repost-weekly-rotation",
        "profile": profile,
        "score": result.score,
        "features": dict(result.features),
        "modifiers": list(result.modifiers),
    }


def create_event(*args: object, **kwargs: object):
    kwargs.setdefault("domain_importance_snapshot", _snapshot())
    return _create_event(*args, **kwargs)


def add_event_mention(*args: object, **kwargs: object):
    if kwargs.get("materiality") == "material_update":
        kwargs.setdefault("domain_importance_snapshot", _snapshot())
    return _add_event_mention(*args, **kwargs)


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
            aggregation_key="patch:lol_pc:26.17:balance",
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
            aggregation_key="activity:lol_pc:star-event",
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
            domain_importance_snapshot=_snapshot(
                "worlds_key",
                scale="major",
                competition_region="lpl",
                prominence="star",
            ),
        )
        assert created is True
        assert item.importance_score == 0.78
        assert event.importance_score == 0.86

        item.current_revision = 2
        item.importance_calculation = {
            "importance_profile": "esports_regular",
            "profile_score": 0.6,
        }
        db.commit()
        event, added = add_event_mention(
            db,
            event_id=event.id,
            normalized_item_id=item.id,
            mention_index=0,
            relation="corrects",
            source_role="independent_media",
            materiality="material_update",
            domain_importance_snapshot=_snapshot(
                "esports_regular", competition_region="lck"
            ),
            current_summary="第二修订更正了数值。",
            latest_development="消息第二修订",
        )
        assert added is True
        assert event.current_revision == 2
        assert event.message_count_total == 1
        assert event.importance_score == 0.86

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
        assert [
            mention.domain_importance_snapshot["score"] for mention in mentions
        ] == [0.86, 0.6]
