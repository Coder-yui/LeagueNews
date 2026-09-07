import asyncio
import json
from pathlib import Path

from app.orchestration.experiments import (
    CaseResult,
    ExperimentCase,
    ExperimentDataset,
    ExperimentPlan,
    ExperimentRunner,
    ExperimentTarget,
    FrozenExperimentExecutor,
    LocalExperimentRunStore,
    default_evaluators,
    load_frozen_dataset,
)
from app.orchestration.experiments.evaluators import FrozenTaskEvaluator


FIXTURE_ROOT = Path(__file__).parents[1] / "evals" / "phase3_fixture"


def _plan(name: str) -> ExperimentPlan:
    path = FIXTURE_ROOT / "plans" / f"{name}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    manifest = (path.parent / payload.pop("dataset_manifest")).resolve()
    payload["dataset"] = load_frozen_dataset(manifest).model_dump(mode="json")
    return ExperimentPlan.model_validate(payload)


def _runner(plan: ExperimentPlan, tmp_path: Path | None = None) -> ExperimentRunner:
    store = (
        LocalExperimentRunStore(tmp_path, plan.experiment_id)
        if tmp_path is not None
        else None
    )
    return ExperimentRunner(
        {plan.dataset.target: FrozenExperimentExecutor()},
        evaluators=default_evaluators(),
        state_store=store,
    )


def test_method_configuration_changes_actual_fixture_execution() -> None:
    plan = _plan("featured")
    report = asyncio.run(_runner(plan).run(plan))

    first = report.candidate_results[0].cases[0].actual
    second = report.candidate_results[1].cases[0].actual
    assert first["featured_plan"]["selected_item_ids"] == [101]
    assert second["featured_plan"]["selected_item_ids"] == [101, 102]
    assert first["featured_plan"]["decisions"]
    assert report.candidate_results[1].cases[0].metadata["method_calls"][0][
        "strategy_parameters"
    ]["min_importance_score"] == 0.65


def test_stateful_failure_invalidates_dependent_steps_and_isolated_candidates() -> None:
    plan = _plan("events")
    report = asyncio.run(_runner(plan).run(plan))

    for candidate_result in report.candidate_results:
        failed_scenario = candidate_result.cases[1]
        assert failed_scenario.status == "invalid"
        assert failed_scenario.actual["steps"][0]["status"] == "failed"
        assert failed_scenario.actual["steps"][1]["status"] == "invalid"
        assert failed_scenario.metadata["state_isolation_key"].startswith(
            f"{plan.experiment_id}:{candidate_result.candidate.candidate_id}:"
        )


def test_frozen_item_runs_actual_graph_without_online_publication() -> None:
    plan = _plan("item_processing")
    report = asyncio.run(_runner(plan).run(plan))
    result = report.candidate_results[0].cases[0]
    assert result.status == "succeeded", result.error_message
    assert result.actual["outcome"] == "preview_completed"
    assert result.actual["importance"]["importance_score"] > 0
    assert result.actual["translation"]["status"] == "not_required"
    assert "publication" not in result.actual
    assert result.metadata["artifact_scope"] == "experiment"
    assert result.metadata["entry_stage"] == "raw_item"


def test_answers_are_removed_before_executor_and_cache_follows_fingerprint(
    tmp_path: Path,
) -> None:
    plan = _plan("message_analysis")

    class AnswerFreeExecutor(FrozenExperimentExecutor):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def execute(self, *, case, candidate, context):
            self.calls += 1
            assert case.expected is None
            assert case.labels.values == {}
            assert case.label_source.value == "unlabeled"
            return await super().execute(case=case, candidate=candidate, context=context)

    executor = AnswerFreeExecutor()
    store = LocalExperimentRunStore(tmp_path, plan.experiment_id)
    runner = ExperimentRunner(
        {plan.dataset.target: executor},
        evaluators=default_evaluators(),
        state_store=store,
    )
    first = asyncio.run(runner.run(plan))
    assert executor.calls == 4
    assert all(not case.cache_hit for result in first.candidate_results for case in result.cases)

    second = asyncio.run(runner.run(plan))
    assert executor.calls == 4
    assert all(case.cache_hit for result in second.candidate_results for case in result.cases)

    relabeled_case = plan.dataset.cases[0].model_copy(
        update={
            "labels": plan.dataset.cases[0].labels.model_copy(
                update={"ambiguity": ["review pending"]}
            )
        }
    )
    relabeled_dataset = plan.dataset.model_copy(
        update={"cases": [relabeled_case, *plan.dataset.cases[1:]]}
    )
    assert relabeled_dataset.fingerprint != plan.dataset.fingerprint
    assert relabeled_dataset.execution_fingerprint == plan.dataset.execution_fingerprint

    changed = plan.model_copy(
        update={"dataset": plan.dataset.model_copy(update={"version": "changed"})}
    )
    third = asyncio.run(runner.run(changed))
    assert executor.calls == 8
    assert all(not case.cache_hit for result in third.candidate_results for case in result.cases)
    assert third.dataset_fingerprint != first.dataset_fingerprint


def test_human_metrics_are_separate_from_fixture_diagnostics() -> None:
    dataset = ExperimentDataset(
        name="human-metric-test",
        version="v1",
        target=ExperimentTarget.IMPORTANCE_SCORING,
        input_schema_version="v1",
        cases=[
            ExperimentCase(
                case_id="one",
                input={"evidence": "frozen"},
                labels={
                    "values": {
                        "scale": "major",
                        "importance_score": 0.9,
                    },
                    "source": "human_confirmed",
                    "schema_version": "labels-v1",
                    "evidence_basis": ["reviewer note"],
                },
            )
        ],
    )
    result = FrozenTaskEvaluator(ExperimentTarget.IMPORTANCE_SCORING).evaluate(
        dataset=dataset,
        cases=[
            CaseResult(
                case_id="one",
                status="succeeded",
                actual={
                    "importance": {"scale": "major"},
                    "importance_score": 0.8,
                },
            )
        ],
    )

    assert result["gold_eligible"] == 1
    assert result["pending_human_review"] == 0
    assert result["gold_metrics"]["field_accuracy"] == {"scale": 1.0}
    assert result["gold_metrics"]["score_mae"] == 0.1


def test_call_budget_is_enforced_without_running_extra_cases() -> None:
    plan = _plan("importance").model_copy(update={"max_total_calls": 2, "max_concurrency": 1})
    report = asyncio.run(_runner(plan).run(plan))

    assert report.run_metadata["max_total_calls"] == 2
    assert report.run_metadata["total_calls"] == 2
    assert sum(result.succeeded for result in report.candidate_results) == 1
    assert sum(result.failed for result in report.candidate_results) == 3
    assert any(
        case.error_type == "ExperimentBudgetExceeded"
        for result in report.candidate_results
        for case in result.cases
    )
