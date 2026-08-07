import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, local
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from app.models.event import Event, EventMessage, EventRevision
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.services import event_aggregation
from app.services.event_aggregation import add_message_to_event, create_event

pytestmark = pytest.mark.postgres


@pytest.mark.skipif(
    not os.getenv("EVENT_TEST_DATABASE_URL"),
    reason="EVENT_TEST_DATABASE_URL is not configured",
)
def test_concurrent_message_update_creates_one_membership_and_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(os.environ["EVENT_TEST_DATABASE_URL"], pool_pre_ping=True)
    suffix = uuid4().hex
    source_id: int | None = None
    raw_item_ids: list[int] = []
    event_id: int | None = None

    try:
        with Session(engine, expire_on_commit=False) as db:
            source = Source(
                name=f"event-concurrency-{suffix}",
                connector_type="manual",
            )
            db.add(source)
            db.flush()
            source_id = source.id

            items: list[NormalizedItem] = []
            for index, published_at in enumerate(
                (
                    datetime(2026, 6, 16, tzinfo=UTC),
                    datetime(2026, 6, 17, tzinfo=UTC),
                )
            ):
                raw_item = RawItem(
                    source_id=source.id,
                    external_id=f"{suffix}-{index}",
                    native_title=f"Concurrency item {index}",
                    content_blocks=[
                        {
                            "id": "b0001",
                            "type": "paragraph",
                            "text": f"Concurrency item {index}",
                        }
                    ],
                    published_at=published_at,
                )
                db.add(raw_item)
                db.flush()
                raw_item_ids.append(raw_item.id)
                item = NormalizedItem(
                    raw_item_id=raw_item.id,
                    normalized_title=f"Concurrency item {index}",
                    normalized_text="test",
                    summary="test",
                    entities=[],
                    primary_topic="other",
                    subtopic="other",
                    source_kind="unknown",
                    information_stage="update",
                    content_form="original",
                    product_scope="uncertain",
                    importance_score=0.5,
                    target_language="zh-CN",
                    translated_content_blocks=[],
                    translation_status="not_required",
                    analysis_model="test",
                    analysis_version="test",
                )
                db.add(item)
                db.flush()
                items.append(item)
            db.commit()

            event = create_event(
                db,
                normalized_item_id=items[0].id,
                aggregation_key=f"test:concurrency:{suffix}",
                title="Concurrency event",
                summary="Initial",
                event_kind="other",
                aggregation_strategy="singleton",
                product_scope="uncertain",
            )
            event_id = event.id
            second_item_id = items[1].id

        barrier = Barrier(2)
        thread_state = local()
        original_membership_lookup = event_aggregation._existing_membership

        def synchronized_membership_lookup(
            db: Session,
            normalized_item_id: int,
            lookup_event_id: int,
        ) -> EventMessage | None:
            membership = original_membership_lookup(
                db,
                normalized_item_id,
                lookup_event_id,
            )
            if not getattr(thread_state, "initial_lookup_complete", False):
                thread_state.initial_lookup_complete = True
                barrier.wait()
            return membership

        monkeypatch.setattr(
            event_aggregation,
            "_existing_membership",
            synchronized_membership_lookup,
        )

        def attach() -> bool:
            with Session(engine, expire_on_commit=False) as session:
                _, added = add_message_to_event(
                    session,
                    event_id=event_id,
                    normalized_item_id=second_item_id,
                    summary="Updated",
                )
                return added

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: attach(), range(2)))

        assert sorted(results) == [False, True]
        with Session(engine) as db:
            assert db.scalar(
                select(func.count(EventMessage.event_id)).where(
                    EventMessage.event_id == event_id
                )
            ) == 2
            assert db.scalar(
                select(func.count(EventRevision.id)).where(
                    EventRevision.event_id == event_id
                )
            ) == 2
            assert db.get(Event, event_id).current_revision == 2
    finally:
        with Session(engine) as db:
            if event_id is not None:
                event = db.get(Event, event_id)
                if event is not None:
                    db.delete(event)
                    db.flush()
            if raw_item_ids:
                db.execute(delete(RawItem).where(RawItem.id.in_(raw_item_ids)))
            if source_id is not None:
                db.execute(delete(Source).where(Source.id == source_id))
            db.commit()
        engine.dispose()
