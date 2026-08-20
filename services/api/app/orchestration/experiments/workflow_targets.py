import hashlib
from datetime import date
from typing import Any

from app.orchestration.catalog import GraphName, GraphRegistry, ImplementationStatus
from app.orchestration.contracts import RunMode
from app.orchestration.daily_report.graph import DailyReportBackend, DailyReportRequest
from app.orchestration.event_aggregation.graph import (
    EventAggregationBackend,
    EventAggregationRequest,
)
from app.orchestration.experiments.contracts import (
    CandidateSpec,
    ExperimentCase,
    ExperimentExecutionContext,
    ExperimentTarget,
)


def _stable_positive_id(*values: str) -> int:
    digest = hashlib.sha256(":".join(values).encode()).digest()
    return int.from_bytes(digest[:8], "big") % 2_000_000_000 + 1


def _graph_definition(
    registry: GraphRegistry,
    candidate: CandidateSpec,
    *,
    target: ExperimentTarget,
    graph_name: GraphName,
):
    if candidate.target != target:
        raise ValueError(f"{target} executor received another experiment target")
    if candidate.graph_name != graph_name:
        raise ValueError(f"candidate must declare {graph_name} graph")
    if candidate.graph_version is None or candidate.state_version is None:
        raise ValueError("candidate must declare an exact graph identity")
    definition = registry.resolve(graph_name, candidate.graph_version)
    if definition.status != ImplementationStatus.IMPLEMENTED:
        raise ValueError("candidate graph is not implemented")
    if definition.state_version != candidate.state_version:
        raise ValueError("candidate state version does not match graph registry")
    return definition


class EventGraphExperimentExecutor:
    def __init__(
        self, *, registry: GraphRegistry, backend: EventAggregationBackend
    ) -> None:
        self._registry = registry
        self._backend = backend

    async def execute(
        self,
        *,
        case: ExperimentCase,
        candidate: CandidateSpec,
        context: ExperimentExecutionContext,
    ) -> dict[str, object]:
        _graph_definition(
            self._registry,
            candidate,
            target=ExperimentTarget.EVENT_AGGREGATION,
            graph_name=GraphName.EVENT_AGGREGATION,
        )
        item_id = _positive_input(case.input, "normalized_item_id")
        revision = _positive_input(case.input, "normalized_item_revision")
        request = EventAggregationRequest(
            workflow_run_id=_stable_positive_id(
                context.experiment_id, context.candidate_id, case.case_id, "run"
            ),
            normalized_item_id=item_id,
            normalized_item_revision=revision,
            run_mode=RunMode.EXPERIMENT,
            batch_id=_stable_positive_id(context.experiment_id, "batch"),
            graph_version=str(candidate.graph_version),
            state_version=int(candidate.state_version),
        )
        graph = self._registry.build(
            GraphName.EVENT_AGGREGATION,
            str(candidate.graph_version),
            backend=self._backend,
        )
        result = await graph.ainvoke({"request": request.model_dump(mode="json")})
        if "__interrupt__" in result:
            raise RuntimeError("experiment case requires unresolved manual review")
        return {
            key: value
            for key, value in result.items()
            if key in {"outcome", "admission", "candidate_retrieval", "semantic_decision"}
        }


class DailyReportGraphExperimentExecutor:
    def __init__(self, *, registry: GraphRegistry, backend: DailyReportBackend) -> None:
        self._registry = registry
        self._backend = backend

    async def execute(
        self,
        *,
        case: ExperimentCase,
        candidate: CandidateSpec,
        context: ExperimentExecutionContext,
    ) -> dict[str, object]:
        _graph_definition(
            self._registry,
            candidate,
            target=ExperimentTarget.DAILY_REPORT,
            graph_name=GraphName.DAILY_REPORT_GENERATION,
        )
        try:
            report_date = date.fromisoformat(str(case.input["report_date"]))
        except (KeyError, ValueError) as exc:
            raise ValueError("experiment case requires ISO report_date") from exc
        request = DailyReportRequest(
            workflow_run_id=_stable_positive_id(
                context.experiment_id, context.candidate_id, case.case_id, "run"
            ),
            report_date=report_date,
            run_mode=RunMode.EXPERIMENT,
            batch_id=_stable_positive_id(context.experiment_id, "batch"),
            graph_version=str(candidate.graph_version),
            state_version=int(candidate.state_version),
        )
        graph = self._registry.build(
            GraphName.DAILY_REPORT_GENERATION,
            str(candidate.graph_version),
            backend=self._backend,
        )
        result = await graph.ainvoke({"request": request.model_dump(mode="json")})
        if "__interrupt__" in result:
            raise RuntimeError("experiment case requires unresolved manual review")
        return {
            key: value
            for key, value in result.items()
            if key in {"outcome", "selected", "deduplicated", "assigned", "ranked"}
        }


def _positive_input(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"experiment case requires positive {key}")
    return value
