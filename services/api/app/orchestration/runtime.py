from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command
from sqlalchemy.orm import Session

from app.orchestration.catalog import GraphName, GraphRegistry, create_v3_graph_registry
from app.methods import MethodAssembly, MethodAssemblyConfig, MethodCallRecord
from app.orchestration.daily_report import (
    DAILY_REPORT_GRAPH_VERSION,
    DailyReportRequest,
    DailyReportBackendV3,
)
from app.orchestration.event_aggregation import (
    EVENT_AGGREGATION_GRAPH_VERSION,
    EventAggregationRequest,
    EventAggregationBackendV3,
)
from app.orchestration.item_processing import ItemProcessingBackendV3
from app.orchestration.contracts import (
    ITEM_PROCESSING_GRAPH_VERSION,
    ItemProcessingRequest,
)
from app.services.llm import LLMClient
from app.services.call_metering import measured_run
from app.services.pipeline_execution import PipelineExecutionGuard


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
        execution_guard: PipelineExecutionGuard | None = None,
        method_config: MethodAssemblyConfig | None = None,
        method_assembly: MethodAssembly | None = None,
    ) -> None:
        self._registry = registry or create_v3_graph_registry()
        self._checkpointer = checkpointer
        self._method_assembly = method_assembly or MethodAssembly(method_config)
        self._item_backend = ItemProcessingBackendV3(
            session_factory,
            llm_factory=llm_factory,
            execution_guard=execution_guard,
            method_assembly=self._method_assembly,
        )
        self._event_backend = EventAggregationBackendV3(
            session_factory,
            llm_factory=llm_factory,
            execution_guard=execution_guard,
            method_assembly=self._method_assembly,
        )
        self._daily_backend = DailyReportBackendV3(
            session_factory,
            method_assembly=self._method_assembly,
        )

    @measured_run
    async def invoke_item(
        self,
        request: ItemProcessingRequest,
        *,
        command: Command | None = None,
        resume_existing: bool = False,
    ) -> dict[str, Any]:
        graph = self._registry.build(
            GraphName.ITEM_PROCESSING,
            request.graph_version,
            backend=self._item_backend,
            checkpointer=self._checkpointer,
        )
        return await graph.ainvoke(
            (
                command
                if command is not None
                else None
                if resume_existing
                else {"request": request.model_dump(mode="json"), "trace": []}
            ),
            config=_config(request.thread_id),
        )

    @measured_run
    async def invoke_event(
        self,
        request: EventAggregationRequest,
        *,
        resume_existing: bool = False,
    ) -> dict[str, Any]:
        graph = self._registry.build(
            GraphName.EVENT_AGGREGATION,
            request.graph_version,
            backend=self._event_backend,
            checkpointer=self._checkpointer,
        )
        return await graph.ainvoke(
            (
                None
                if resume_existing
                else {"request": request.model_dump(mode="json"), "trace": []}
            ),
            config=_config(request.thread_id),
        )

    @measured_run
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

    @property
    def method_calls(self) -> tuple[MethodCallRecord, ...]:
        """Actual method selections made since this runtime was created."""

        return self._method_assembly.calls


def _config(thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": thread_id}}
