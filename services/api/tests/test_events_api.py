from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.api.routes.events import get_event, list_events
from app.core.database import Base
from app.models.media_asset import MediaAsset
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.schemas.event import EventDetailRead, EventPageRead
from app.services.event_metrics import refresh_event_metrics
from app.services.events import add_event_mention, create_event


def _item(
    db: Session,
    *,
    source: Source,
    external_id: str,
    title: str,
    content_form: str = "original",
) -> NormalizedItem:
    raw = RawItem(
        source_id=source.id,
        external_id=external_id,
        native_title=title,
        canonical_url=f"https://example.com/{external_id}",
        content_blocks=[{"type": "paragraph", "text": title}],
        published_at=datetime.now(UTC),
    )
    db.add(raw)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw.id,
        normalized_title=title,
        normalized_text=title,
        summary=f"{title}摘要",
        entities=[{"type": "patch", "canonical_id": "patch:26.17"}],
        products=["lol_pc"],
        message_type="game_announcement",
        topics=["balance_gameplay"],
        content_form=content_form,
        importance_score=0.5,
        importance_calculation={"importance_profile": "gameplay_announcement"},
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


def test_event_list_and_detail_present_current_projection_and_material_timeline() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        leaker = Source(name="Presentation leaker", reliability_score=0.8)
        official = Source(
            name="Presentation official",
            is_official=True,
            reliability_score=1,
        )
        community = Source(name="Presentation community", reliability_score=0.4)
        db.add_all([leaker, official, community])
        db.flush()
        origin = _item(db, source=leaker, external_id="origin", title="平衡爆料")
        discussion = _item(
            db,
            source=community,
            external_id="discussion",
            title="普通讨论",
            content_form="quote",
        )
        confirmation = _item(
            db,
            source=official,
            external_id="confirmation",
            title="官网确认",
        )
        db.add(
            MediaAsset(
                raw_item_id=confirmation.raw_item_id,
                block_index=0,
                source_url="https://example.com/image.jpg",
                storage_path="/private/image.jpg",
                public_path="/media/image.jpg",
                visibility="published",
            )
        )
        db.commit()

        event, _ = create_event(
            db,
            normalized_item_id=origin.id,
            mention_index=0,
            event_family="gameplay_balance",
            products=["lol_pc"],
            canonical_anchors={"patch_version": "patch:26.17"},
            title="26.17 平衡调整",
            current_summary="爆料称将进行平衡调整。",
            source_role="known_leaker",
            relation="reports",
            independence_group=f"source:{leaker.id}",
            evidence_excerpt="首次爆料",
        )
        add_event_mention(
            db,
            event_id=event.id,
            normalized_item_id=discussion.id,
            mention_index=0,
            relation="mentions",
            source_role="ordinary_account",
            materiality="context_only",
            evidence_excerpt="普通讨论",
        )
        add_event_mention(
            db,
            event_id=event.id,
            normalized_item_id=confirmation.id,
            mention_index=0,
            relation="confirms",
            source_role="responsible_official",
            materiality="material_update",
            independence_group=f"source:{official.id}",
            evidence_excerpt="官网确认",
            current_summary="官网确认 26.17 平衡调整。",
            latest_development="官网确认",
        )
        refresh_event_metrics(db, {event.id})
        db.commit()

        page = EventPageRead.model_validate(
            list_events(
                product="lol_pc",
                event_family=None,
                lifecycle=None,
                credibility_level=None,
                importance_level=None,
                heat_level=None,
                search=None,
                sort_by="latest",
                limit=25,
                offset=0,
                db=db,
            )
        )
        detail = EventDetailRead.model_validate(get_event(event.id, db))

        assert page.total == 1
        assert page.items[0].current_summary == "官网确认 26.17 平衡调整。"
        assert page.items[0].category == "lol_pc"
        assert page.items[0].message_count == 3
        assert page.items[0].source_count == 3
        assert page.items[0].credibility_level == "officially_confirmed"
        assert page.items[0].primary_source.source_name == "Presentation official"
        assert page.items[0].best_media_url == "/media/image.jpg"
        assert detail.references == {
            "origin_message_id": origin.id,
            "primary_source_message_id": confirmation.id,
            "latest_update_message_id": confirmation.id,
            "best_media_message_id": confirmation.id,
        }
        assert len(detail.timeline) == 2
        assert {node.message_id for node in detail.timeline} == {origin.id, confirmation.id}
        assert {node.message_revision for node in detail.timeline} == {1}
        assert len(detail.related_messages) == 3
        assert len(detail.evidence) == 3
        assert {evidence.message_revision for evidence in detail.evidence} == {1}


def test_event_api_category_filter_is_server_side_and_counts_distinct_messages() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        pc_source = Source(name="PC filter source")
        esports_source = Source(name="Esports filter source")
        db.add_all([pc_source, esports_source])
        db.flush()
        pc = _item(db, source=pc_source, external_id="pc-filter", title="PC event")
        esports = _item(db, source=esports_source, external_id="esports-filter", title="Esports event")
        db.commit()
        pc_event, _ = create_event(
            db,
            normalized_item_id=pc.id,
            mention_index=0,
            event_family="gameplay_release",
            products=["lol_pc"],
            canonical_anchors={"release_name": "pc:event"},
            title="PC event",
            current_summary="PC event",
        )
        esports_event, _ = create_event(
            db,
            normalized_item_id=esports.id,
            mention_index=0,
            event_family="esports_match",
            products=["lol_esports"],
            canonical_anchors={"match": "match:event"},
            title="Esports event",
            current_summary="Esports event",
        )
        add_event_mention(
            db,
            event_id=pc_event.id,
            normalized_item_id=pc.id,
            mention_index=1,
            relation="mentions",
            source_role="ordinary_account",
            materiality="context_only",
        )
        page = EventPageRead.model_validate(
            list_events(
                category="lol_pc",
                product=None,
                event_family=None,
                lifecycle=None,
                credibility_level=None,
                importance_level=None,
                heat_level=None,
                search=None,
                sort_by="latest",
                limit=25,
                offset=0,
                db=db,
            )
        )
        assert page.total == 1
        assert page.items[0].id == pc_event.id
        assert page.items[0].message_count == 1
        assert page.items[0].source_count == 1
        assert esports_event.id != pc_event.id


def test_official_denial_updates_credibility_and_lifecycle_without_deleting_event() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        leaker = Source(name="Denied event leaker", reliability_score=0.8)
        official = Source(name="Denied event official", is_official=True, reliability_score=1)
        db.add_all([leaker, official])
        db.flush()
        rumor = _item(db, source=leaker, external_id="rumor", title="未确认传闻")
        denial = _item(db, source=official, external_id="denial", title="官方否认")
        db.commit()
        event, _ = create_event(
            db,
            normalized_item_id=rumor.id,
            mention_index=0,
            event_family="gameplay_release",
            products=["lol_pc"],
            canonical_anchors={"champion": "champion:false"},
            title="未确认新英雄传闻",
            current_summary="存在一条未确认传闻。",
            lifecycle_status="unconfirmed",
            relation="reports",
            source_role="known_leaker",
            independence_group=f"source:{leaker.id}",
        )
        add_event_mention(
            db,
            event_id=event.id,
            normalized_item_id=denial.id,
            mention_index=0,
            relation="denies",
            source_role="responsible_official",
            materiality="material_update",
            independence_group=f"source:{official.id}",
            current_summary="官方已否认该传闻。",
            latest_development="官方否认",
        )
        refresh_event_metrics(db, {event.id})
        db.commit()

        detail = EventDetailRead.model_validate(get_event(event.id, db))

        assert detail.credibility_level == "denied"
        assert detail.credibility_score == 0
        assert detail.lifecycle_status == "denied"
        assert detail.current_summary == "官方已否认该传闻。"
        assert len(detail.evidence) == 2
