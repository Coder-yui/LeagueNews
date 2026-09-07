from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.orchestration.contracts import (
    ITEM_PROCESSING_GRAPH,
    ITEM_PROCESSING_GRAPH_VERSION,
    ITEM_PROCESSING_STATE_VERSION,
    PROCESSING_STAGE_ORDER,
)
from app.orchestration.item_processing import build_item_processing_graph
from app.orchestration.event_aggregation import (
    EVENT_AGGREGATION_GRAPH_VERSION,
    EVENT_AGGREGATION_STAGE_ORDER,
    EVENT_AGGREGATION_STATE_VERSION,
    build_event_aggregation_graph,
)
from app.orchestration.daily_report import (
    DAILY_REPORT_GRAPH_VERSION,
    DAILY_REPORT_STAGE_ORDER,
    DAILY_REPORT_STATE_VERSION,
    build_daily_report_graph,
)


class GraphName(StrEnum):
    ITEM_PROCESSING = ITEM_PROCESSING_GRAPH
    EVENT_AGGREGATION = "event_aggregation"
    DAILY_REPORT_GENERATION = "daily_report_generation"


@dataclass(frozen=True, slots=True)
class GraphDefinition:
    name: GraphName
    graph_version: str
    state_version: int
    stages: tuple[str, ...]
    builder: Callable[..., Any]

    def __post_init__(self) -> None:
        if not self.graph_version.strip():
            raise ValueError("graph_version cannot be empty")
        if self.state_version < 1:
            raise ValueError("state_version must be positive")
        if not self.stages or len(self.stages) != len(set(self.stages)):
            raise ValueError("graph stages must be non-empty and unique")


class GraphRegistry:
    def __init__(self) -> None:
        self._definitions: dict[tuple[GraphName, str], GraphDefinition] = {}

    def register(self, definition: GraphDefinition) -> None:
        key = (definition.name, definition.graph_version)
        if key in self._definitions:
            raise ValueError(
                f"graph already registered: {definition.name}:{definition.graph_version}"
            )
        self._definitions[key] = definition

    def resolve(self, name: GraphName, graph_version: str) -> GraphDefinition:
        try:
            return self._definitions[(name, graph_version)]
        except KeyError as exc:
            raise KeyError(f"unknown graph version: {name}:{graph_version}") from exc

    def build(self, name: GraphName, graph_version: str, **kwargs: Any) -> Any:
        definition = self.resolve(name, graph_version)
        return definition.builder(**kwargs)

    def definitions(self) -> tuple[GraphDefinition, ...]:
        return tuple(
            sorted(
                self._definitions.values(),
                key=lambda value: (value.name.value, value.graph_version),
            )
        )


def create_v3_graph_registry() -> GraphRegistry:
    registry = GraphRegistry()
    registry.register(
        GraphDefinition(
            name=GraphName.ITEM_PROCESSING,
            graph_version=ITEM_PROCESSING_GRAPH_VERSION,
            state_version=ITEM_PROCESSING_STATE_VERSION,
            stages=tuple(stage.value for stage in PROCESSING_STAGE_ORDER),
            builder=build_item_processing_graph,
        )
    )
    registry.register(
        GraphDefinition(
            name=GraphName.EVENT_AGGREGATION,
            graph_version=EVENT_AGGREGATION_GRAPH_VERSION,
            state_version=EVENT_AGGREGATION_STATE_VERSION,
            stages=tuple(stage.value for stage in EVENT_AGGREGATION_STAGE_ORDER),
            builder=build_event_aggregation_graph,
        )
    )
    registry.register(
        GraphDefinition(
            name=GraphName.DAILY_REPORT_GENERATION,
            graph_version=DAILY_REPORT_GRAPH_VERSION,
            state_version=DAILY_REPORT_STATE_VERSION,
            stages=tuple(stage.value for stage in DAILY_REPORT_STAGE_ORDER),
            builder=build_daily_report_graph,
        )
    )
    return registry
