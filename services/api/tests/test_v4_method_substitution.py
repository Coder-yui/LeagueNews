import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.orchestration.daily_report import DailyReportBackendV3
from app.orchestration.daily_report.graph import DailyCandidate, DailyCandidateSet
from app.orchestration.experiments import (
    ExperimentPlan,
    ExperimentRunner,
    FrozenExperimentExecutor,
    load_frozen_dataset,
)
from app.methods import (
    FeaturedCandidate,
    MethodAssembly,
    MethodAssemblyConfig,
)


ROOT = Path(__file__).parents[1]
PHASE4_PLANS = ROOT / "evals" / "phase4_fixture" / "plans"


def _phase4_plan(name: str) -> ExperimentPlan:
    path = PHASE4_PLANS / f"{name}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    manifest = (path.parent / payload.pop("dataset_manifest")).resolve()
    payload["dataset"] = load_frozen_dataset(manifest).model_dump(mode="json")
    return ExperimentPlan.model_validate(payload)


def _run(plan: ExperimentPlan):
    return asyncio.run(
        ExperimentRunner(
            {plan.dataset.target: FrozenExperimentExecutor()}
        ).run(plan)
    )


@pytest.mark.parametrize(
    "plan_name,expected_method",
    [
        ("message_analysis", "heuristic"),
        ("importance", "rule_v2"),
        ("events", "token_recall_v2"),
        ("featured", "top_n"),
        ("daily_report", "balanced"),
    ],
)
def test_phase4_candidate_uses_real_implementation(
    plan_name: str, expected_method: str
) -> None:
    plan = _phase4_plan(plan_name)
    report = _run(plan)
    candidate = report.candidate_results[1]
    successful = [case for case in candidate.cases if case.status == "succeeded"]

    assert successful
    assert any(
        call["implementation"] == expected_method
        for case in successful
        for call in case.metadata["method_calls"]
    )
    assert all(
        case.metadata["online_business_writes"] == 0 for case in successful
    )


def test_phase4_component_outputs_differ_from_baseline() -> None:
    plans = {
        name: _run(_phase4_plan(name))
        for name in ("message_analysis", "importance", "events", "featured", "daily_report")
    }

    message_base = plans["message_analysis"].candidate_results[0].cases[0].actual
    message_candidate = plans["message_analysis"].candidate_results[1].cases[0].actual
    assert message_base != message_candidate

    importance_base = plans["importance"].candidate_results[0].cases[0].actual
    importance_candidate = plans["importance"].candidate_results[1].cases[0].actual
    assert importance_base != importance_candidate

    event_base = plans["events"].candidate_results[0].cases[0].actual
    event_candidate = plans["events"].candidate_results[1].cases[0].actual
    assert event_base != event_candidate

    featured_base = plans["featured"].candidate_results[0].cases[0].actual
    featured_candidate = plans["featured"].candidate_results[1].cases[0].actual
    assert featured_base != featured_candidate

    daily_base = plans["daily_report"].candidate_results[0].cases[0].actual
    daily_candidate = plans["daily_report"].candidate_results[1].cases[0].actual
    assert daily_base != daily_candidate


def test_phase4_end_to_end_propagates_component_changes() -> None:
    path = ROOT / "evals" / "phase4_fixture" / "end_to_end" / "plan.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    manifest = (path.parent / payload.pop("dataset_manifest")).resolve()
    payload["dataset"] = load_frozen_dataset(manifest).model_dump(mode="json")
    plan = ExperimentPlan.model_validate(payload)
    report = _run(plan)

    baseline = report.candidate_results[0].cases[0]
    candidate = report.candidate_results[1].cases[0]
    assert baseline.status == candidate.status == "succeeded"
    assert baseline.actual["featured_plan"] != candidate.actual["featured_plan"]
    assert candidate.metadata["upstream_mode"] == "self"
    assert candidate.metadata["entry_stage"] == "message_analysis"
    assert candidate.metadata["online_business_writes"] == 0
    assert {
        call["implementation"]
        for call in candidate.metadata["method_calls"]
    } >= {"heuristic", "rule_v2", "token_recall_v2", "top_n", "balanced"}


def test_online_daily_backend_uses_the_same_assembly_without_publishing() -> None:
    backend = DailyReportBackendV3(
        lambda: None,  # plan does not open a database session
        method_assembly=MethodAssembly(
            MethodAssemblyConfig(
                daily_report="balanced",
                strategy_parameters={"daily_report": {"section_limits": {"lolpc": 1}}},
            )
        ),
    )
    sections = DailyCandidateSet(
        candidates=[
                DailyCandidate(
                    message_id=1,
                    importance_score=0.8,
                    published_at=datetime(2026, 9, 7, 1, tzinfo=UTC),
                    content_form="original",
                    products=["lol_pc"],
                )
        ]
    )
    ranked = asyncio.run(backend.plan(sections))

    assert [item.message_id for item in ranked.sections["lolpc"]] == [1]
    assert backend._method_assembly.last_call("daily_report").implementation == "balanced"


def test_candidate_methods_are_database_and_queue_independent() -> None:
    assembly = MethodAssembly(MethodAssemblyConfig(featured_selection="top_n"))
    plan = assembly.select_featured(
        [
            FeaturedCandidate(
                normalized_item_id=1,
                importance_score=0.91,
                content_form="original",
            )
        ]
    )
    assert plan.selected_item_ids == (1,)
