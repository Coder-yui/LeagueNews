from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.orm import Session

from app.orchestration.catalog import GraphName, GraphRegistry, create_v3_graph_registry
from app.orchestration.daily_report import (
    DAILY_REPORT_GRAPH_VERSION,
    DailyReportRequest,
    V2CompatibilityDailyReportBackend,
)
from app.orchestration.event_aggregation import (
    EVENT_AGGREGATION_GRAPH_VERSION,
    EventAggregationRequest,
    V2CompatibilityEventBackend,
)
from app.orchestration.item_processing import V2CompatibilityItemBackend
from app.orchestration.contracts import (
    ITEM_PROCESSING_GRAPH_VERSION,
    ItemProcessingRequest,
)
from app.services.llm import LLMClient


SessionFactory = Callable[[], Session]
LLMFactory = Callable[[], LLMClient]


class LeagueNewsWorkflowRuntime:
    """One composition root for local V3 workflow execution.

    Queue leasing, HTTP review commands and scheduling remain outside this
    class. They submit versioned requests; this runtime owns graph selection,
    backend wiring and technical checkpoint configuration.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        llm_factory: LLMFactory = LLMClient,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        registry: GraphRegistry | None = None,
    ) -> None:
        self._registry = registry or create_v3_graph_registry()
        self._checkpointer = checkpointer
        self._item_backend = V2CompatibilityItemBackend(
            session_factory, llm_factory=llm_factory
        )
        self._event_backend = V2CompatibilityEventBackend(
            session_factory, llm_factory=llm_factory
        )
        self._daily_backend = V2CompatibilityDailyReportBackend(session_factory)

    async def invoke_item(
        self, request: ItemProcessingRequest
    ) -> dict[str, Any]:
        graph = self._registry.build(
            GraphName.ITEM_PROCESSING,
            request.graph_version,
            backend=self._item_backend,
            checkpointer=self._checkpointer,
        )
        return await graph.ainvoke(
            {"request": request.model_dump(mode="json"), "trace": []},
            config=_config(request.thread_id),
        )

    async def invoke_event(
        self, request: EventAggregationRequest
    ) -> dict[str, Any]:
        graph = self._registry.build(
            GraphName.EVENT_AGGREGATION,
            request.graph_version,
            backend=self._event_backend,
            checkpointer=self._checkpointer,
        )
        return await graph.ainvoke(
            {"request": request.model_dump(mode="json"), "trace": []},
            config=_config(request.thread_id),
        )

    async def invoke_daily_report(
        self, request: DailyReportRequest
    ) -> dict[str, Any]:
        graph = self._registry.build(
            GraphName.DAILY_REPORT_GENERATION,
            request.graph_version,
            backend=self._daily_backend,
            checkpointer=self._checkpointer,
        )
        return await graph.ainvoke(
            {"request": request.model_dump(mode="json"), "trace": []},
            config=_config(request.thread_id),
        )

    @property
    def versions(self) -> dict[str, str]:
        return {
            GraphName.ITEM_PROCESSING.value: ITEM_PROCESSING_GRAPH_VERSION,
            GraphName.EVENT_AGGREGATION.value: EVENT_AGGREGATION_GRAPH_VERSION,
            GraphName.DAILY_REPORT_GENERATION.value: DAILY_REPORT_GRAPH_VERSION,
        }


def _config(thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": thread_id}}
