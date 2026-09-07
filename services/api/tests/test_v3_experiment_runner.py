import asyncio
import json
from pathlib import Path

import pytest

from app.orchestration.experiments import (
    CandidateSpec,
    ExperimentCase,
    ExperimentDataset,
    ExperimentPlan,
    ExperimentRunner,
    ExperimentTarget,
    LocalExperimentArtifactStore,
)
from app.orchestration.experiments.contracts import ExperimentExecutionContext


class RecordingExecutor:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.contexts: list[ExperimentExecutionContext] = []

    async def execute(self, *, case, candidate, context):
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.contexts.append(context)
        try:
            await asyncio.sleep(0.01)
            if case.input.get("fail"):
                raise RuntimeError("isolated fixture failure")
            return {
                "value": case.input["value"],
                "candidate": candidate.candidate_id,
            }
        finally:
            self.active -= 1


class SlowExecutor:
    async def execute(self, *, case, candidate, context):
        await asyncio.sleep(0.05)
        return {"case": case.case_id, "candidate": candidate.candidate_id}


class ExactMatchForRunnerTest:
    def evaluate(self, *, dataset, cases):
        expected_by_id = {
            case.case_id: case.expected
            for case in dataset.cases
            if case.expected is not None
        }
        labeled_results = [case for case in cases if case.case_id in expected_by_id]
        matched = sum(
            case.status == "succeeded"
            and case.actual == expected_by_id[case.case_id]
            for case in labeled_results
        )
        return {
            "labeled": len(labeled_results),
            "matched": matched,
            "exact_match": matched / len(labeled_results) if labeled_results else None,
        }


def _plan() -> ExperimentPlan:
    return ExperimentPlan(
        experiment_id="importance-comparison-001",
        dataset=ExperimentDataset(
            name="message-importance-golden",
            version="v1",
            target=ExperimentTarget.MESSAGE_IMPORTANCE,
            input_schema_version="importance-input-v1",
            cases=[
                ExperimentCase(
                    case_id="case-a",
                    input={"value": 1},
                    expected={"value": 1, "candidate": "importance-v12-a"},
                ),
                ExperimentCase(case_id="case-b", input={"value": 2, "fail": True}),
                ExperimentCase(case_id="case-c", input={"value": 3}),
            ],
        ),
        candidates=[
            CandidateSpec(
                candidate_id="importance-v12-a",
                target=ExperimentTarget.MESSAGE_IMPORTANCE,
                component_versions={
                    "importance_policy": "importance-v12-a",
                    "prompt": "importance-prompt-v15-a",
                },
            ),
            CandidateSpec(
                candidate_id="importance-v12-b",
                target=ExperimentTarget.MESSAGE_IMPORTANCE,
                component_versions={
                    "importance_policy": "importance-v12-b",
                    "prompt": "importance-prompt-v15-b",
                },
            ),
        ],
        max_concurrency=2,
    )


def test_experiment_runner_bounds_concurrency_and_isolates_case_failures() -> None:
    executor = RecordingExecutor()
    plan = _plan()
    runner = ExperimentRunner(
        {ExperimentTarget.MESSAGE_IMPORTANCE: executor},
        evaluators={ExperimentTarget.MESSAGE_IMPORTANCE: ExactMatchForRunnerTest()},
    )

    report = asyncio.run(runner.run(plan))

    assert executor.peak == 2
    assert [result.candidate.candidate_id for result in report.candidate_results] == [
        "importance-v12-a",
        "importance-v12-b",
    ]
    for result in report.candidate_results:
        assert result.succeeded == 2
        assert result.failed == 1
        assert [case.case_id for case in result.cases] == [
            "case-a",
            "case-b",
            "case-c",
        ]
        assert result.cases[1].error_type == "RuntimeError"
    assert report.candidate_results[0].metrics == {
        "labeled": 1,
        "matched": 1,
        "exact_match": 1.0,
    }
    assert report.candidate_results[1].metrics == {
        "labeled": 1,
        "matched": 0,
        "exact_match": 0.0,
    }
    assert all(context.run_mode == "experiment" for context in executor.contexts)
    assert all(context.publication_allowed is False for context in executor.contexts)
    assert {context.dataset_fingerprint for context in executor.contexts} == {
        plan.dataset.fingerprint
    }


def test_local_artifact_store_is_atomic_and_refuses_accidental_overwrite(
    tmp_path: Path,
) -> None:
    plan = _plan()
    report = asyncio.run(
        ExperimentRunner(
            {ExperimentTarget.MESSAGE_IMPORTANCE: RecordingExecutor()}
        ).run(plan)
    )
    store = LocalExperimentArtifactStore(tmp_path)

    directory = store.write(plan=plan, report=report)

    assert json.loads((directory / "plan.json").read_text())["dataset"]["version"] == "v1"
    assert json.loads((directory / "report.json").read_text())[
        "dataset_fingerprint"
    ] == plan.dataset.fingerprint
    assert not list(directory.glob("*.tmp"))
    with pytest.raises(FileExistsError, match="already exists"):
        store.write(plan=plan, report=report)


def test_experiment_plan_rejects_mixed_targets_and_partial_graph_identity() -> None:
    plan = _plan()
    with pytest.raises(ValueError, match="candidate target must match"):
        ExperimentPlan(
            experiment_id="mixed",
            dataset=plan.dataset,
            candidates=[
                CandidateSpec(
                    candidate_id="events",
                    target=ExperimentTarget.EVENT_AGGREGATION,
                )
            ],
        )
    with pytest.raises(ValueError, match="move together"):
        CandidateSpec(
            candidate_id="partial",
            target=ExperimentTarget.ITEM_PROCESSING,
            graph_name="item_processing",
        )
    with pytest.raises(ValueError, match="string_pattern_mismatch"):
        ExperimentPlan(
            experiment_id="../escape",
            dataset=plan.dataset,
            candidates=plan.candidates,
        )


def test_experiment_case_timeout_is_isolated() -> None:
    plan = _plan().model_copy(update={"case_timeout_seconds": 0.01})
    report = asyncio.run(
        ExperimentRunner(
            {ExperimentTarget.MESSAGE_IMPORTANCE: SlowExecutor()}
        ).run(plan)
    )

    assert all(result.failed == 3 for result in report.candidate_results)
    assert all(
        case.error_type == "TimeoutError"
        for result in report.candidate_results
        for case in result.cases
    )
