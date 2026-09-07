"""Acceptance tests for real method requests, online parity and isolated replay."""

import asyncio
import json
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models import Event, EventMention, NotificationOutbox, Source
from app.orchestration.daily_report.backend import DailyReportBackendV3
from app.orchestration.daily_report.graph import DailyReportRequest, build_daily_report_graph
from app.orchestration.contracts import RunMode
from app.orchestration.experiments import FrozenExperimentExecutor
from app.orchestration.experiments.contracts import (
    CandidateSpec,
    ExperimentCase,
    ExperimentExecutionContext,
    ScenarioStep,
)
from app.methods import (
    MethodAssembly,
    MethodAssemblyConfig,
    MessageAnalysisInput,
    DailyReportPlan,
    FeaturedPlan,
    FeaturedCandidate,
)
from app.services.daily_reports import load_daily_candidates
from app.services.notifications import enqueue_featured_message
from app.services.published_items import search_published_items
from app.services.llm import execution_metadata
from test_llm import _client_with_responses
from test_v3_daily_report_backend import _database, _message


def _context():
    return ExperimentExecutionContext(
        experiment_id="parity", candidate_id="baseline", dataset_fingerprint="0" * 64
    )


def test_prompt_and_model_configuration_reach_actual_transport_and_audit():
    response = json.dumps(
        {"title": "测试", "summary": "测试摘要", "products": ["lol_pc"], "content_form": "original"}
    )
    client, transport = _client_with_responses([response, response])
    for index in (1, 2):
        ref = f"analysis-{index}"
        assembly = MethodAssembly(
            MethodAssemblyConfig(
                prompt_refs={"message_analysis": ref},
                prompt_contents={ref: f"实际提示词 {index}"},
                model_parameters={
                    "message_analysis": {
                        "model": f"model-{index}",
                        "temperature": index / 10,
                        "max_tokens": 300 + index,
                    }
                },
            )
        )
        result = asyncio.run(
            assembly.analyze_message(
                MessageAnalysisInput(title="测试", content="测试正文"), client=client
            )
        )
        request = transport.calls[-1]
        assert request["model"] == f"model-{index}"
        assert request["temperature"] == index / 10
        assert request["max_tokens"] == 300 + index
        assert f"实际提示词 {index}" in request["messages"][0]["content"]
        metadata = execution_metadata(result)
        assert metadata["model"] == request["model"]
        assert metadata["temperature"] == request["temperature"]
        assert metadata["prompt_name"] == ref
    assert not hasattr(client, "_request_parameters")


def test_missing_prompt_and_unsupported_model_options_fail_before_request():
    client, transport = _client_with_responses([])
    for config in [
        MethodAssemblyConfig(prompt_refs={"message_analysis": "missing"}),
        MethodAssemblyConfig(model_parameters={"message_analysis": {"ignored_option": True}}),
    ]:
        with pytest.raises(ValueError):
            asyncio.run(
                MethodAssembly(config).analyze_message(MessageAnalysisInput(), client=client)
            )
    assert not transport.calls


def test_daily_graph_receives_complete_candidates_and_matches_offline_method():
    factory = _database()
    assembly = MethodAssembly()
    with factory() as db:
        source = Source(name="daily-parity")
        db.add(source)
        db.flush()
        older = _message(
            db,
            source_id=source.id,
            external_id="older",
            score=0.9,
            published_at=datetime(2026, 8, 20, 1, tzinfo=UTC),
        )
        newer = _message(
            db,
            source_id=source.id,
            external_id="newer",
            score=0.8,
            published_at=datetime(2026, 8, 20, 2, tzinfo=UTC),
        )
        low = _message(
            db,
            source_id=source.id,
            external_id="low",
            score=0.2,
            published_at=datetime(2026, 8, 20, 3, tzinfo=UTC),
        )
        event = Event(
            title="同一事件",
            current_summary="摘要",
            products=["lol_pc"],
            event_family="gameplay_release",
        )
        db.add(event)
        db.flush()
        for item in [older, newer]:
            db.add(
                EventMention(
                    event_id=event.id,
                    normalized_item_id=item.id,
                    normalized_item_revision=1,
                    mention_index=0,
                )
            )
        db.commit()
        candidates = load_daily_candidates(db, date(2026, 8, 20))
        assert len(candidates) == 3
        newer_id, low_id = newer.id, low.id
    assembly = MethodAssembly(MethodAssemblyConfig(daily_report="balanced"))
    expected = assembly.plan_daily_report(candidates)
    request = DailyReportRequest(
        workflow_run_id=1, report_date=date(2026, 8, 20), run_mode=RunMode.EXPERIMENT, batch_id=1
    )
    graph = build_daily_report_graph(DailyReportBackendV3(factory, method_assembly=assembly))
    result = asyncio.run(graph.ainvoke({"request": request.model_dump(mode="json")}))
    assert result["ranked"] == expected.model_dump(mode="json")
    assert [row["message_id"] for row in result["ranked"]["sections"]["lolpc"]] == [newer_id]

    def choose_low(*, candidates, selection):
        assert any(row.message_id == low_id for row in candidates)
        row = next(row for row in candidates if row.message_id == low_id)
        return DailyReportPlan(
            sections={
                "lolpc": (
                    {
                        "message_id": row.message_id,
                        "importance_score": row.importance_score,
                        "published_at": row.published_at.isoformat(),
                        "content_form": row.content_form,
                        "products": list(row.products),
                        "event_ids": [],
                    },
                )
            }
        )

    custom = MethodAssembly(
        MethodAssemblyConfig(daily_report="low"),
        implementations={"daily_report": {"low": choose_low}},
    )
    result = asyncio.run(
        build_daily_report_graph(DailyReportBackendV3(factory, method_assembly=custom)).ainvoke(
            {"request": request.model_dump(mode="json")}
        )
    )
    assert result["ranked"]["sections"]["lolpc"][0]["message_id"] == low_id


def test_featured_policy_controls_both_public_reads_and_notification_queue(monkeypatch):
    monkeypatch.setattr(
        settings,
        "processing_method_config",
        {
            "featured_selection": "top_n",
            "strategy_parameters": {"featured_selection": {"max_items": 1}},
        },
    )
    monkeypatch.setattr(settings, "feishu_featured_push_enabled", True)
    # Only queues notifications; no delivery worker is started.
    factory = _database()
    assembly = MethodAssembly()
    with factory() as db:
        source = Source(name="featured-parity")
        db.add(source)
        db.flush()
        first = _message(
            db,
            source_id=source.id,
            external_id="first",
            score=0.9,
            published_at=datetime(2026, 8, 20, 1, tzinfo=UTC),
        )
        second = _message(
            db,
            source_id=source.id,
            external_id="second",
            score=0.8,
            published_at=datetime(2026, 8, 20, 2, tzinfo=UTC),
        )
        db.commit()
        assert [
            row["id"]
            for row in search_published_items(db, featured=True, assembly=assembly).items
        ] == [first.id]
        assert enqueue_featured_message(db, first, assembly=assembly)
        assert not enqueue_featured_message(db, second, assembly=assembly)
        assert len(list(db.scalars(select(NotificationOutbox)))) == 1


def test_sync_registration_dispatches_without_editing_assembly():
    def custom(*, candidates, selection):
        return FeaturedPlan(selected_item_ids=(23,))

    assembly = MethodAssembly(
        MethodAssemblyConfig(featured_selection="custom"),
        implementations={"featured_selection": {"custom": custom}},
    )
    assert assembly.select_featured(
        [FeaturedCandidate(normalized_item_id=23, importance_score=0.3, content_form="original")]
    ).selected_item_ids == (23,)


def _event_step(step_id, timestamp, decision):
    return ScenarioStep(
        step_id=step_id,
        received_at=timestamp,
        input={
            "message": {
                "title": "新模式公布",
                "text": "英雄联盟新模式上线",
                "products": ["lol_pc"],
                "topics": ["gameplay"],
            },
            "fixture_outputs": {"event_aggregation": {"baseline": {"mentions": [decision]}}},
        },
    )


def test_event_scenario_creates_then_attaches_and_preserves_latest_projection():
    create = {
        "mention_index": 0,
        "action": "create",
        "product": "lol_pc",
        "event_family": "gameplay_release",
        "evidence_excerpt": "新模式上线",
        "new_event": {"title": "新模式", "summary": "新模式上线"},
    }
    attach = {
        "mention_index": 0,
        "action": "attach",
        "event_id": 1,
        "product": "lol_pc",
        "event_family": "gameplay_release",
        "evidence_excerpt": "补充上线日期",
        "projection": {"summary": "最新进展"},
    }
    late = {**attach, "projection": {"summary": "迟到的旧消息"}}
    case = ExperimentCase(
        case_id="scenario",
        input={},
        steps=[
            _event_step("create", "2026-09-07T01:00:00+00:00", create),
            _event_step("attach", "2026-09-07T03:00:00+00:00", attach),
            _event_step("late", "2026-09-07T02:00:00+00:00", late),
        ],
    )
    executor = FrozenExperimentExecutor()
    candidate = CandidateSpec(candidate_id="baseline", target="event_aggregation")
    first = asyncio.run(
        executor.execute_scenario(case=case, candidate=candidate, context=_context())
    )
    second = asyncio.run(
        executor.execute_scenario(case=case, candidate=candidate, context=_context())
    )
    assert first["final_state"] == second["final_state"]
    assert len(first["final_state"]["candidates"]) == 1
    assert len(first["final_state"]["memberships"]) == 3
    assert first["final_state"]["candidates"][0]["current_summary"] == "最新进展"
    assert first["steps"][1]["actual"]["recalled_candidates"][0]["event_id"] == 1


def test_experiment_uses_injected_client_instead_of_embedded_baseline_response():
    class ActualClient:
        async def analyze_message_content(self, **payload):
            from app.services.llm import MessageContentAnalysisResult

            return MessageContentAnalysisResult(
                title=payload["title"],
                summary="实际执行",
                products=["lol_pc"],
                content_form="original",
            )

    case = ExperimentCase(case_id="provider", input={"title": "输入标题", "content": "输入正文"})
    result = asyncio.run(
        FrozenExperimentExecutor(client_factory=lambda _: ActualClient()).execute(
            case=case,
            candidate=CandidateSpec(candidate_id="baseline", target="message_analysis"),
            context=_context(),
        )
    )
    assert result["message_analysis"]["summary"] == "实际执行"


def test_full_raw_to_daily_experiment_uses_real_item_graph_and_scores():
    from pathlib import Path
    from app.orchestration.experiments import ExperimentPlan, ExperimentRunner, load_frozen_dataset

    folder = Path(__file__).parents[1] / "evals/phase4_fixture/raw_to_daily"
    payload = json.loads((folder / "plan.json").read_text())
    payload.pop("dataset_manifest")
    payload["dataset"] = load_frozen_dataset(folder / "manifest.json").model_dump(mode="json")
    plan = ExperimentPlan.model_validate(payload)
    report = asyncio.run(
        ExperimentRunner({plan.dataset.target: FrozenExperimentExecutor()}).run(plan)
    )
    for candidate in report.candidate_results:
        case = candidate.cases[0]
        assert case.status == "succeeded", case.error_message
        assert case.metadata["entry_stage"] == "raw_item"
        assert case.metadata["complete_flow"] is True
        assert len(case.actual["message_results"]) == 2
        applied = sum(
            mention["action"] != "ignore"
            for row in case.actual["message_results"]
            for mention in row["event_decision"]["mentions"]
        )
        assert len(case.actual["event_state"]["memberships"]) == applied
        if candidate.candidate.candidate_id == "baseline-composed":
            assert applied == 2
        assert case.actual["message_results"][0]["importance_score"] > 0.85


def test_scoring_formula_can_be_replaced_without_changing_execution_facilities():
    from app.domain.message_scoring import calculate_message_importance
    from app.services.llm import MessageClassificationImportanceResult

    result = MessageClassificationImportanceResult(
        message_type="game_patch_notes",
        topics=["balance_gameplay"],
        scale="major",
        audience_region="global",
        competition_region="none",
        prominence="normal",
        skin_tier="none",
        is_bulk_update=False,
        evidence=["版本说明"],
    )

    def alternate(*, payload, selection):
        values = calculate_message_importance(**payload)
        values["importance_score"] = 0.61
        values["calculation"] = {
            **values["calculation"],
            "policy_version": "experiment-formula",
            "final_score": 0.61,
        }
        return values

    assembly = MethodAssembly(
        MethodAssemblyConfig(importance_calculation="alternate"),
        implementations={"importance_calculation": {"alternate": alternate}},
    )

    class Client:
        async def classify_and_score_importance(self, **kwargs):
            return result

    executor = FrozenExperimentExecutor(
        client_factory=lambda _: Client(), assembly_factory=lambda _: assembly
    )
    actual = asyncio.run(
        executor.execute(
            case=ExperimentCase(
                case_id="scoring", input={"content": "版本说明", "content_form": "original"}
            ),
            candidate=CandidateSpec(candidate_id="alternate", target="importance_scoring"),
            context=_context(),
        )
    )
    assert actual["importance_score"] == 0.61
    assert actual["calculation"]["policy_version"] == "experiment-formula"


def test_future_initial_event_evidence_is_rejected_before_replay():
    from app.orchestration.experiments.event_store import ExperimentEventStore

    with pytest.raises(ValueError, match="beyond visible_at"):
        ExperimentEventStore(
            {"candidates": [{"last_seen_at": "2026-09-08T00:00:00+00:00"}]},
            visible_at="2026-09-07T00:00:00+00:00",
        )
