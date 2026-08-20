from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.models.event import EventMention, EventRevision
from app.models.normalized_item import NormalizedItem, NormalizedItemRevision
from app.models.raw_item import RawItem
from app.models.source import Source
from app.schemas.editorial import (
    EventEditorialPatch,
    ManualEventRevisionCommand,
    ManualMessageRevisionCommand,
    ManualRevisionMetadata,
    MessageEditorialPatch,
)
from app.services.editorial_revisions import (
    EditorialRevisionConflictError,
    revise_published_event,
    revise_published_message,
)
from app.services.events import add_event_mention, create_event


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _item(db: Session, source: Source, external_id: str) -> NormalizedItem:
    raw = RawItem(
        source_id=source.id,
        external_id=external_id,
        native_title="immutable source",
        content_blocks=[{"type": "paragraph", "text": "immutable source"}],
        published_at=datetime.now(UTC),
    )
    db.add(raw)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw.id,
        normalized_title="自动标题",
        normalized_text="自动正文",
        summary="自动摘要",
        entities=[],
        products=["lol_pc"],
        message_type="game_announcement",
        topics=["balance_gameplay"],
        content_form="original",
        importance_score=0.5,
        translated_content_blocks=[],
        translation_status="not_required",
        analysis_model="fixture",
        analysis_version="fixture",
    )
    db.add(item)
    db.flush()
    return item


def _metadata(key: str) -> ManualRevisionMetadata:
    return ManualRevisionMetadata(
        editor_id="admin:1",
        reason="编辑确认后的最终结果",
        idempotency_key=key,
    )


def test_manual_message_revision_is_direct_audited_and_preserves_membership() -> None:
    with _session() as db:
        source = Source(name="editorial source", reliability_score=0.8)
        db.add(source)
        db.flush()
        item = _item(db, source, "message-1")
        event, _created = create_event(
            db,
            normalized_item_id=item.id,
            mention_index=0,
            event_family="gameplay_balance",
            products=["lol_pc"],
            canonical_anchors={"patch": "26.17"},
            title="自动事件",
            current_summary="自动事件摘要",
        )
        raw_blocks = list(item.raw_item.content_blocks)

        command = ManualMessageRevisionCommand(
            normalized_item_id=item.id,
            expected_revision=1,
            patch=MessageEditorialPatch(
                normalized_title="人工最终标题",
                summary="人工最终摘要",
                importance_score=0.9,
            ),
            metadata=_metadata("message-revision-0001"),
        )
        result = revise_published_message(db, command)

        assert result.revision == 2
        assert item.normalized_title == "人工最终标题"
        assert item.manual_override_fields == [
            "importance_score",
            "normalized_title",
            "summary",
        ]
        assert item.importance_calculation["manual_override"] == {
            "score": 0.9,
            "reason": "编辑确认后的最终结果",
            "editor_id": "admin:1",
        }
        assert item.raw_item.content_blocks == raw_blocks
        revision = db.scalar(
            select(NormalizedItemRevision).where(
                NormalizedItemRevision.normalized_item_id == item.id,
                NormalizedItemRevision.revision == 2,
            )
        )
        assert revision is not None
        assert revision.revision_source == "manual"
        assert revision.editor_id == "admin:1"
        assert revision.snapshot["normalized_title"] == "人工最终标题"
        active_mention = db.scalar(
            select(EventMention).where(
                EventMention.event_id == event.id,
                EventMention.normalized_item_id == item.id,
                EventMention.normalized_item_revision == 2,
            )
        )
        assert active_mention is not None

        replay = revise_published_message(db, command)
        assert replay.idempotent_replay is True
        assert replay.revision == 2


def test_manual_revision_uses_optimistic_locking() -> None:
    with _session() as db:
        source = Source(name="conflict source")
        db.add(source)
        db.flush()
        item = _item(db, source, "message-conflict")
        item.current_revision = 2
        db.commit()

        with pytest.raises(EditorialRevisionConflictError, match="changed from 1 to 2"):
            revise_published_message(
                db,
                ManualMessageRevisionCommand(
                    normalized_item_id=item.id,
                    expected_revision=1,
                    patch=MessageEditorialPatch(summary="过期编辑"),
                    metadata=_metadata("message-revision-0002"),
                ),
            )


def test_manual_event_fields_survive_later_automatic_aggregation() -> None:
    with _session() as db:
        source = Source(name="event editorial source", reliability_score=0.8)
        db.add(source)
        db.flush()
        origin = _item(db, source, "event-origin")
        update = _item(db, source, "event-update")
        event, _created = create_event(
            db,
            normalized_item_id=origin.id,
            mention_index=0,
            event_family="gameplay_balance",
            products=["lol_pc"],
            canonical_anchors={"patch": "26.17"},
            title="自动事件",
            current_summary="自动摘要",
        )

        result = revise_published_event(
            db,
            ManualEventRevisionCommand(
                event_id=event.id,
                expected_revision=1,
                patch=EventEditorialPatch(
                    title="人工最终事件",
                    current_summary="人工最终事件摘要",
                ),
                metadata=_metadata("event-revision-0001"),
            ),
        )
        assert result.revision == 2

        add_event_mention(
            db,
            event_id=event.id,
            normalized_item_id=update.id,
            mention_index=0,
            relation="reports",
            source_role="unknown",
            materiality="material_update",
            title="后续自动标题",
            current_summary="后续自动摘要",
        )

        assert event.title == "人工最终事件"
        assert event.current_summary == "人工最终事件摘要"
        assert event.current_revision == 3
        manual_revision = db.scalar(
            select(EventRevision).where(
                EventRevision.event_id == event.id,
                EventRevision.revision == 2,
            )
        )
        assert manual_revision is not None
        assert manual_revision.revision_source == "manual"
        assert manual_revision.editor_id == "admin:1"
