import asyncio
import pytest
from app.orchestration.experiments.contracts import (
    ExperimentCase,
    ExperimentDataset,
    ExperimentPlan,
    CandidateSpec,
)
from app.orchestration.experiments.runner import ExperimentRunner
from app.orchestration.experiments.artifacts import LocalExperimentRunStore
from app.services.call_metering import CallAttempt
from types import SimpleNamespace


class Executor:
    async def execute(self, *, case, candidate, context):
        assert case.label_values == {} and case.tags == [] and case.expected is None
        meter = CallAttempt(
            logical_call_id="test-call",
            attempt=1,
            provider="synthetic",
            model="test",
            operation="analysis",
            parameters={},
            prompt_hash="hash",
            schema_hash="hash",
            input_hash="hash",
        )
        meter.response(SimpleNamespace(usage={"prompt_tokens": 10, "completion_tokens": 5}))
        meter.emit("succeeded")
        return {
            "message_analysis": {"products": ["lol_pc"]},
            "_experiment_metadata": {"call_count": 1},
        }


def plan():
    return ExperimentPlan(
        experiment_id="test",
        dataset=ExperimentDataset(
            name="test",
            version="1",
            target="message_analysis",
            input_schema_version="1",
            cases=[
                ExperimentCase(
                    case_id="one",
                    input={},
                    expected={"products": ["lol_pc"]},
                    tags=["answer-label"],
                )
            ],
        ),
        candidates=[CandidateSpec(candidate_id="baseline", target="message_analysis")],
    )


def test_cache_has_no_current_measurement_and_preserves_history(tmp_path):
    runner = ExperimentRunner(
        {"message_analysis": Executor()}, state_store=LocalExperimentRunStore(tmp_path, "test")
    )
    first = asyncio.run(runner.run(plan())).candidate_results[0].cases[0]
    second = asyncio.run(runner.run(plan())).candidate_results[0].cases[0]
    assert first.token_count == 15 and first.cost_usd is None
    assert second.cache_hit and second.token_count == second.call_count == second.cost_usd == 0
    assert second.duration_ms is None
    assert second.metadata["application_attempt_count"] == 0
    assert second.metadata["attempts"] == []
    assert second.metadata["historical_measurement"]["token_count"] == 15
    assert len(second.metadata["historical_measurement"]["attempts"]) == 1


def test_unsupported_cost_budget_fails_before_execution():
    with pytest.raises(ValueError, match="cost budgets are not implemented"):
        asyncio.run(
            ExperimentRunner({"message_analysis": Executor()}).run(
                plan().model_copy(update={"max_cost_usd": 1})
            )
        )


def test_hidden_annotation_fields_are_rejected():
    with pytest.raises(ValueError, match="annotation fields"):
        ExperimentCase(case_id="one", input={"context": {"expected": "answer"}}).for_execution()


def test_embedded_dataset_rejects_cross_split_story():
    with pytest.raises(ValueError, match="crosses data splits"):
        ExperimentDataset(
            name="test",
            version="1",
            target="message_analysis",
            input_schema_version="1",
            cases=[
                ExperimentCase(case_id="one", input={}, group_id="story", split="train"),
                ExperimentCase(case_id="two", input={}, group_id="story", split="test"),
            ],
        )


def test_continuous_end_to_end_keeps_event_and_distribution_state():
    import json
    from pathlib import Path
    from app.orchestration.experiments.contracts import ExperimentExecutionContext, ScenarioStep
    from app.orchestration.experiments.frozen_executor import FrozenExperimentExecutor

    payload = json.loads(
        (Path(__file__).parents[1] / "evals/phase4_fixture/raw_to_daily/cases.jsonl").read_text()
    )["input"]
    case = ExperimentCase(
        case_id="continuous",
        input={},
        initial_state=payload["initial_state"],
        steps=[
            ScenarioStep(
                step_id=str(message["message_id"]),
                received_at=message["raw_item"]["published_at"],
                input={"messages": [message]},
            )
            for message in payload["messages"]
        ],
    )
    actual = asyncio.run(
        FrozenExperimentExecutor().execute_scenario(
            case=case,
            candidate=CandidateSpec(candidate_id="baseline", target="end_to_end"),
            context=ExperimentExecutionContext(
                experiment_id="continuous", candidate_id="baseline", dataset_fingerprint="0" * 64
            ),
        )
    )
    assert [step["status"] for step in actual["steps"]] == ["succeeded", "succeeded"]
    assert len(actual["final_state"]["memberships"]) == 2
    ids = {
        row["message_id"]
        for rows in actual["steps"][-1]["actual"]["daily_plan"]["sections"].values()
        for row in rows
    }
    # 302 scores 0.56 and remains ineligible under the real daily policy.
    # The eligible message from the preceding step must still be selected.
    assert ids == {301}


def test_future_initial_event_snapshot_is_rejected():
    from app.orchestration.experiments.event_store import ExperimentEventStore
    with pytest.raises(ValueError, match="beyond visible_at"):
        ExperimentEventStore({"candidates": [{"last_seen_at": "2026-09-12T00:00:00+00:00"}]},
                             visible_at="2026-09-11T00:00:00+00:00")
