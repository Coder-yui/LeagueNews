import asyncio
import pytest
from datetime import UTC, datetime

from app.methods import MethodAssembly, MethodAssemblyConfig
from app.methods.retrieval import EventRetrievalQuery, RetrievedEvent
from app.orchestration.experiments.event_store import ExperimentEventStore
from app.orchestration.experiments.frozen_executor import _FixtureClient


@pytest.mark.parametrize("revision", [1, 99])
def test_fake_async_retrieval_runs_with_closed_input_session_and_real_membership(revision):
    store = ExperimentEventStore(
        {
            "candidates": [
                {
                    "event_id": 17,
                    "title": "新模式",
                    "event_family": "gameplay_release",
                    "products": ["lol_pc"],
                    "last_seen_at": "2026-09-07T00:00:00+00:00",
                }
            ]
        }
    )
    queries = []
    from sqlalchemy import event

    active = []
    event.listen(store.engine, "checkout", lambda *args: active.append(True))
    event.listen(store.engine, "checkin", lambda *args: active.pop())

    class FakeRetriever:
        async def retrieve(self, query: EventRetrievalQuery):
            assert not active
            await asyncio.sleep(0)
            assert not active
            queries.append(query)
            return (
                RetrievedEvent(
                    event_id=17,
                    revision=revision,
                    title="新模式",
                    event_family="gameplay_release",
                    products=("lol_pc",),
                    retrieval_source="fake-async",
                ),
            )

    assembly = MethodAssembly(MethodAssemblyConfig(), event_retriever=FakeRetriever())
    client = _FixtureClient(
        {
            "fixture_outputs": {
                "event_aggregation": {
                    "baseline": {
                        "mentions": [
                            {
                                "mention_index": 0,
                                "action": "attach",
                                "event_id": 17,
                                "event_family": "gameplay_release",
                                "product": "lol_pc",
                                "evidence_excerpt": "新模式上线",
                            }
                        ]
                    }
                }
            }
        }
    )
    try:
        if revision == 99:
            from app.services.event_method_support import SupersededEventAggregationError

            with pytest.raises(SupersededEventAggregationError, match="revision changed"):
                result = asyncio.run(
                    store.process(
                        {
                            "title": "新模式",
                            "text": "新模式上线",
                            "products": ["lol_pc"],
                            "topics": ["gameplay"],
                            "published_at": "2026-09-07T01:00:00+00:00",
                        },
                        assembly=assembly,
                        client=client,
                    )
                )
            assert store.snapshot()["memberships"] == []
            return
        result = asyncio.run(
            store.process(
                {
                    "title": "新模式",
                    "text": "新模式上线",
                    "products": ["lol_pc"],
                    "topics": ["gameplay"],
                    "published_at": "2026-09-07T01:00:00+00:00",
                },
                assembly=assembly,
                client=client,
            )
        )
        assert result["event_ids"] == [17]
        assert result["event_memberships"] == [{"event_id": 17, "mention_index": 0}]
        assert queries[0].visible_until == datetime(2026, 9, 7, 1, tzinfo=UTC)
        assert "event_retriever" not in assembly.config.model_dump()
    finally:
        store.close()


def test_method_config_is_a_deep_snapshot():
    config = MethodAssemblyConfig(strategy_parameters={"event_recall": {"total_limit": 2}})
    left, right = MethodAssembly(config), MethodAssembly(config)
    left.config.strategy_parameters["event_recall"]["total_limit"] = 9
    assert right.select("event_recall").strategy_parameters == {"total_limit": 2}
    assert config.strategy_parameters["event_recall"]["total_limit"] == 2


def test_sql_pool_preserves_baseline_ranking_and_business_snapshots():
    from app.services.event_candidates import RuleEventRetriever, load_event_pool

    store = ExperimentEventStore(
        {
            "candidates": [
                {
                    "event_id": 1,
                    "title": "新模式",
                    "event_family": "gameplay_release",
                    "products": ["lol_pc"],
                    "last_seen_at": "2026-09-06T00:00:00+00:00",
                    "key_facts": [{"fact": "开放"}],
                },
                {
                    "event_id": 2,
                    "title": "旧模式",
                    "event_family": "gameplay_release",
                    "products": ["lol_pc"],
                    "last_seen_at": "2025-01-01T00:00:00+00:00",
                },
            ]
        }
    )
    assembly = MethodAssembly(MethodAssemblyConfig())
    query = EventRetrievalQuery(
        title="新模式",
        content="开放",
        products=("lol_pc",),
        published_at=datetime(2026, 9, 7, tzinfo=UTC),
        visible_until=datetime(2026, 9, 7, tzinfo=UTC),
        possible_families=("gameplay_release",),
    )
    try:
        with store.factory() as db:
            expected = assembly.recall_events(
                message=query.message(),
                candidates=load_event_pool(db),
                possible_families=query.possible_families,
                entity_hints={},
            )
        actual = asyncio.run(RuleEventRetriever(store.factory, assembly).retrieve(query))
        assert [
            {
                key: value
                for key, value in row.model_dump(mode="json").items()
                if key not in {"revision", "retrieval_source"}
            }
            for row in actual
        ] == expected
    finally:
        store.close()
