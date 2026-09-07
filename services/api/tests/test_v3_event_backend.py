import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

import app.models  # noqa: F401
from app.core.database import Base
from app.models.event import Event, EventAggregationRun, EventMention
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.methods import MethodAssembly
from app.orchestration.contracts import RunMode
from app.orchestration.event_aggregation import (
    EventAggregationBackendV3,
    EventAggregationRequest,
    build_event_aggregation_graph,
    create_event_aggregation_run,
)
from app.orchestration.event_aggregation.graph import (
    EventMembershipResult,
    EventProjectionResult,
)
from app.schemas.event_aggregation import EventAggregationResult
from app.orchestration.event_aggregation.service import (
    publish_normalized_item_downstream,
)


def test_event_membership_and_projection_contracts_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        EventMembershipResult(applied_count=0, unexpected=True)
    with pytest.raises(ValidationError):
        EventProjectionResult(refreshed_event_ids=[], unexpected=True)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("graph_name", "legacy_event_aggregation"),
        ("graph_version", "v2.0.0"),
        ("state_version", 99),
        ("thread_id", "legacy-thread"),
    ),
)
def test_event_resume_rejects_mismatched_run_identity(
    field: str,
    value: object,
) -> None:
    factory = _database()
    item_id, revision = _published_item(factory)
    request = create_event_aggregation_run(
        factory,
        normalized_item_id=item_id,
        normalized_item_revision=revision,
        method_config=MethodAssembly().config,
    )
    with factory() as db:
        run = db.get(EventAggregationRun, request.workflow_run_id)
        assert run is not None
        setattr(run, field, value)
        db.commit()

    with factory() as db:
        item = db.get(NormalizedItem, item_id)
        assert item is not None
        with pytest.raises(ValueError, match="identity mismatch"):
            asyncio.run(
                publish_normalized_item_downstream(
                    db,
                    item,
                    session_factory=factory,
                    llm_factory=EventLLM,
                    checkpointer=InMemorySaver(),
                )
            )


def test_completed_event_run_with_old_identity_is_returned_without_resume() -> None:
    factory = _database()
    item_id, revision = _published_item(factory)
    request = create_event_aggregation_run(
        factory,
        normalized_item_id=item_id,
        normalized_item_revision=revision,
        method_config=MethodAssembly().config,
    )
    with factory() as db:
        run = db.get(EventAggregationRun, request.workflow_run_id)
        assert run is not None
        run.status = "completed"
        run.outcome = "applied"
        run.graph_version = "v2.0.0"
        run.state_version = 99
        run.thread_id = "legacy-thread"
        db.commit()

    with factory() as db:
        item = db.get(NormalizedItem, item_id)
        assert item is not None
        returned = asyncio.run(
            publish_normalized_item_downstream(
                db,
                item,
                session_factory=factory,
                llm_factory=EventLLM,
                checkpointer=InMemorySaver(),
            )
        )

    assert returned is not None
    assert returned.id == request.workflow_run_id


class EventLLM:
    async def aggregate_events(self, **_payload: object) -> EventAggregationResult:
        return EventAggregationResult.model_validate(
            {
                "mentions": [
                    {
                        "mention_index": 0,
                        "action": "create",
                        "product": "lol_pc",
                        "event_family": "gameplay_balance",
                        "relation": "reports",
                        "source_role": "responsible_official",
                        "materiality": "material_update",
                        "evidence_excerpt": "26.17 版本平衡调整",
                        "new_event": {
                            "title": "26.17 版本平衡调整",
                            "summary": "26.17 版本平衡调整已经公布。",
                            "canonical_anchors": {"patch_version": "26.17"},
                            "latest_development": "首次公布",
                            "key_facts": [],
                        },
                    }
                ]
            }
        )


def _database():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _published_item(factory: sessionmaker[Session]) -> tuple[int, int]:
    with factory() as db:
        source = Source(name="v3-event-source", is_official=True)
        db.add(source)
        db.flush()
        raw = RawItem(
            source_id=source.id,
            external_id="v3-event-1",
            native_title="26.17 版本平衡调整",
            canonical_url="https://example.com/v3-event-1",
            content_blocks=[
                {"type": "paragraph", "text": "26.17 版本平衡调整已经公布。"}
            ],
            published_at=datetime(2026, 8, 20, 8, tzinfo=UTC),
        )
        db.add(raw)
        db.flush()
        item = NormalizedItem(
            raw_item_id=raw.id,
            normalized_title="26.17 版本平衡调整",
            normalized_text="26.17 版本平衡调整已经公布。",
            summary="26.17 版本平衡调整已经公布。",
            entities=[],
            products=["lol_pc"],
            message_type="game_announcement",
            topics=["balance_gameplay"],
            content_form="original",
            importance_score=0.8,
            importance_calculation={"importance_profile": "gameplay_announcement"},
            translated_title="26.17 版本平衡调整",
            translated_text="26.17 版本平衡调整已经公布。",
            translated_content_blocks=[
                {"type": "paragraph", "text": "26.17 版本平衡调整已经公布。"}
            ],
            translation_status="not_required",
            analysis_model="test",
            analysis_version="test",
            publication_status="published",
        )
        db.add(item)
        db.commit()
        return item.id, item.current_revision


def test_event_graph_reuses_v2_semantics_without_experiment_writes() -> None:
    factory = _database()
    item_id, revision = _published_item(factory)
    backend = EventAggregationBackendV3(
        factory,
        llm_factory=EventLLM,
        method_assembly=MethodAssembly(),
    )
    graph = build_event_aggregation_graph(backend)

    preview = asyncio.run(
        graph.ainvoke(
            {
                "request": EventAggregationRequest(
                    workflow_run_id=9001,
                    normalized_item_id=item_id,
                    normalized_item_revision=revision,
                    run_mode=RunMode.EXPERIMENT,
                        batch_id=7,
                ).model_dump(mode="json")
            }
        )
    )

    assert preview["outcome"] == "preview_completed"
    assert preview["semantic_decision"]["result"]["mentions"][0]["action"] == "create"
    with factory() as db:
        assert db.scalar(select(func.count(Event.id))) == 0
        assert db.scalar(select(func.count(EventAggregationRun.id))) == 0

    request = create_event_aggregation_run(
        factory,
        normalized_item_id=item_id,
        normalized_item_revision=revision,
        method_config=MethodAssembly().config,
    )
    result = asyncio.run(
        graph.ainvoke({"request": request.model_dump(mode="json")})
    )

    assert result["outcome"] == "applied"
    with factory() as db:
        run = db.get(EventAggregationRun, request.workflow_run_id)
        assert run is not None
        assert run.status == "completed"
        assert set(run.decision_draft["stage_checkpoints"]) == {
            "load_message",
            "minimal_filter",
            "candidate_retrieval",
            "semantic_decision",
            "apply_membership",
            "refresh_projection",
        }
        assert db.scalar(select(func.count(Event.id))) == 1
        assert db.scalar(select(func.count(EventMention.id))) == 1


def test_event_service_runs_the_registered_graph_to_completion() -> None:
    factory = _database()
    item_id, _revision = _published_item(factory)
    with factory() as db:
        item = db.get(NormalizedItem, item_id)
        assert item is not None
        run = asyncio.run(
            publish_normalized_item_downstream(
                db,
                item,
                session_factory=factory,
                llm_factory=EventLLM,
                checkpointer=InMemorySaver(),
            )
        )

        assert run is not None
        assert run.status == "completed"
        assert run.outcome == "applied"
        assert db.scalar(select(func.count(Event.id))) == 1
