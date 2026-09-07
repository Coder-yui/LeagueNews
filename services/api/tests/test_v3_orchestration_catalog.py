import pytest

from app.orchestration.catalog import (
    GraphDefinition,
    GraphName,
    GraphRegistry,
    create_v3_graph_registry,
)


def test_v3_catalog_exposes_only_implemented_business_graph_versions() -> None:
    registry = create_v3_graph_registry()

    item = registry.resolve(GraphName.ITEM_PROCESSING, "v3.0.0-dev2")
    event = registry.resolve(GraphName.EVENT_AGGREGATION, "v3.0.0-dev2")
    daily = registry.resolve(GraphName.DAILY_REPORT_GENERATION, "v3.1.0")

    assert item.stages[0] == "evidence"
    assert item.stages[-1] == "publication"
    assert "semantic_decision" in event.stages
    assert "plan" in daily.stages
    assert len(registry.definitions()) == 3


def test_graph_registry_rejects_ambiguous_or_invalid_registration() -> None:
    registry = GraphRegistry()
    definition = GraphDefinition(
        name=GraphName.EVENT_AGGREGATION,
        graph_version="experiment-a",
        state_version=1,
        stages=("one", "two"),
        builder=lambda: "experiment",
    )
    registry.register(definition)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(definition)
    with pytest.raises(KeyError, match="unknown graph version"):
        registry.resolve(GraphName.EVENT_AGGREGATION, "missing")
    with pytest.raises(TypeError):
        GraphDefinition(
            name=GraphName.EVENT_AGGREGATION,
            graph_version="invalid",
            state_version=1,
            stages=("one",),
        )
