import hashlib
from typing import Any

from app.orchestration.catalog import GraphName, GraphRegistry, ImplementationStatus
from app.orchestration.contracts import ItemProcessingRequest, RunMode
from app.orchestration.experiments.contracts import (
    CandidateSpec,
    ExperimentCase,
    ExperimentExecutionContext,
    ExperimentTarget,
)
from app.orchestration.item_processing.graph import ItemProcessingBackend


def _stable_positive_id(*values: str) -> int:
    digest = hashlib.sha256(":".join(values).encode()).digest()
    return int.from_bytes(digest[:8], "big") % 2_000_000_000 + 1


class ItemGraphExperimentExecutor:
    def __init__(
        self,
        *,
        registry: GraphRegistry,
        backend: ItemProcessingBackend,
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
        if candidate.target != ExperimentTarget.ITEM_PROCESSING:
            raise ValueError("item executor received another experiment target")
        if candidate.graph_name != GraphName.ITEM_PROCESSING:
            raise ValueError("item candidate must declare item_processing graph")
        if candidate.graph_version is None or candidate.state_version is None:
            raise ValueError("item candidate must declare an exact graph identity")
        definition = self._registry.resolve(
            GraphName.ITEM_PROCESSING,
            candidate.graph_version,
        )
        if definition.status != ImplementationStatus.IMPLEMENTED:
            raise ValueError("item candidate graph is not implemented")
        if definition.state_version != candidate.state_version:
            raise ValueError("candidate state version does not match graph registry")
        raw_item_id = _positive_input(case.input, "raw_item_id")
        raw_item_revision = _positive_input(case.input, "raw_item_revision")
        workflow_run_id = _stable_positive_id(
            context.experiment_id,
            context.candidate_id,
            case.case_id,
            "run",
        )
        batch_id = _stable_positive_id(context.experiment_id, "batch")
        request = ItemProcessingRequest(
            workflow_run_id=workflow_run_id,
            raw_item_id=raw_item_id,
            raw_item_revision=raw_item_revision,
            run_mode=RunMode.EXPERIMENT,
            batch_id=batch_id,
            graph_version=candidate.graph_version,
            state_version=candidate.state_version,
        )
        graph = self._registry.build(
            GraphName.ITEM_PROCESSING,
            candidate.graph_version,
            backend=self._backend,
        )
        result = await graph.ainvoke(
            {"request": request.model_dump(mode="json"), "trace": []}
        )
        if "__interrupt__" in result:
            raise RuntimeError("experiment case requires unresolved manual review")
        return _public_experiment_output(result)


def _positive_input(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"experiment case requires positive {key}")
    return value


def _public_experiment_output(result: dict[str, Any]) -> dict[str, object]:
    return {
        key: value
        for key, value in result.items()
        if key
        in {
            "outcome",
            "relevance",
            "media",
            "translation",
            "message_analysis",
            "importance",
            "evidence_gate",
        }
    }
