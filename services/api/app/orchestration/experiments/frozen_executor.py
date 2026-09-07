"""Frozen inputs executed through production methods with injectable model I/O.

Raw message and event replay use a disposable SQLite repository. Component
inputs invoke the same assembly directly. No application database is connected.
"""

from __future__ import annotations

from datetime import datetime
from copy import deepcopy
from collections.abc import Callable
from app.domain.event_importance import EventImportanceEvidence, calculate_event_importance
from app.orchestration.experiments.event_store import ExperimentEventStore
from typing import Any

from app.orchestration.experiments.contracts import (
    CandidateSpec,
    ExperimentCase,
    ExperimentExecutionContext,
    ExperimentTarget,
)
from app.methods import (
    EventAggregationInput,
    FeaturedCandidate,
    ImportanceScoringInput,
    MessageAnalysisInput,
    MethodAssembly,
    MethodAssemblyConfig,
    MessageClassificationImportanceResult,
    MessageContentAnalysisResult,
)
from app.schemas.event_aggregation import EventAggregationResult
from app.domain.daily_report import DailyReportCandidate
from app.services.llm import RelevanceResult, TranslationResult


class FixtureExecutionError(RuntimeError):
    pass


class _FixtureClient:
    def __init__(self, input_payload: dict[str, Any]) -> None:
        self._outputs = (
            input_payload.get("fixture_outputs") or input_payload.get("mock_responses") or {}
        )

    def response(self, method: str, implementation: str) -> Any:
        value = self._outputs.get(method, {}) if isinstance(self._outputs, dict) else {}
        if not isinstance(value, dict):
            raise FixtureExecutionError(f"fixture output for {method} must be an object")
        selected = value.get(implementation)
        if selected is None and implementation == "baseline":
            selected = value.get("default", value.get("baseline"))
        if selected is None:
            raise FixtureExecutionError(f"fixture has no response for {method}:{implementation}")
        if not isinstance(selected, dict):
            raise FixtureExecutionError(
                f"fixture response for {method}:{implementation} must be an object"
            )
        if method == "relevance":
            return RelevanceResult.model_validate(selected)
        if method == "translation":
            return TranslationResult.model_validate(selected)
        if method == "message_analysis":
            return MessageContentAnalysisResult.model_validate(selected)
        if method == "importance_scoring":
            return MessageClassificationImportanceResult.model_validate(selected)
        if method == "event_aggregation":
            return EventAggregationResult.model_validate(selected)
        raise FixtureExecutionError(f"unsupported fixture method: {method}")

    def configured(self, **_parameters):
        return self

    async def judge_relevance(self, **_payload):
        return self.response("relevance", "baseline")

    async def translate(self, **_payload):
        return self.response("translation", "baseline")

    async def analyze_message_content(self, **_payload):
        return self.response("message_analysis", "baseline")

    async def classify_and_score_importance(self, **_payload):
        return self.response("importance_scoring", "baseline")

    async def aggregate_events(self, **_payload):
        return self.response("event_aggregation", "baseline")


class FrozenExperimentExecutor:
    """Execute component cases, complete raw-item cases, and ordered event scenarios."""

    def __init__(
        self,
        *,
        default_config: MethodAssemblyConfig | None = None,
        client_factory: Callable[[dict[str, Any]], Any] | None = None,
        assembly_factory: Callable[[MethodAssemblyConfig], MethodAssembly] = MethodAssembly,
    ) -> None:
        self._default_config = default_config or MethodAssemblyConfig()
        self._client_factory = client_factory or _FixtureClient
        self._assembly_factory = assembly_factory

    async def execute(
        self,
        *,
        case: ExperimentCase,
        candidate: CandidateSpec,
        context: ExperimentExecutionContext,
        event_store: ExperimentEventStore | None = None,
    ) -> dict[str, object]:
        del context
        config = _candidate_config(self._default_config, candidate)
        assembly = self._assembly_factory(config)
        payload = deepcopy(case.input)
        target = candidate.target
        if payload.get("force_error"):
            raise FixtureExecutionError("explicit fixture failure")

        if target == ExperimentTarget.END_TO_END:
            return await self._execute_end_to_end(payload=payload, assembly=assembly)

        if target == ExperimentTarget.ITEM_PROCESSING:
            if "raw_item" not in payload:
                raise ValueError(
                    "item_processing requires a frozen raw_item snapshot; use message_analysis for component inputs"
                )
            store = ExperimentEventStore()
            try:
                result = await store.process_item(
                    payload, assembly=assembly, client=self._client_factory(payload)
                )
                return _with_metadata(
                    result,
                    assembly,
                    artifact_scope="experiment",
                    extra_metadata={
                        "entry_stage": "raw_item",
                        "upstream_mode": "self",
                        "complete_flow": True,
                    },
                )
            finally:
                store.close()

        if target == ExperimentTarget.MESSAGE_ANALYSIS:
            analysis = await assembly.analyze_message(
                _message_input(payload), client=self._client_factory(payload)
            )
            return _with_metadata({"message_analysis": analysis.model_dump(mode="json")}, assembly)

        if target in {ExperimentTarget.IMPORTANCE_SCORING, ExperimentTarget.MESSAGE_IMPORTANCE}:
            importance = await assembly.invoke(
                "importance_scoring",
                client=self._client_factory(payload),
                payload=_importance_input(payload).model_dump(mode="json"),
            )
            actual = {"importance": importance.model_dump(mode="json")}
            actual.update(
                assembly.calculate_importance(
                    result=importance,
                    content_form=str(payload.get("content_form", "original")),
                    scoring_content="\n".join(
                        filter(
                            None,
                            [
                                str(payload.get("extracted_facts", {}).get("title", "")),
                                _importance_input(payload).content,
                            ],
                        )
                    ),
                )
            )
            return _with_metadata(actual, assembly)

        if target == ExperimentTarget.EVENT_IMPORTANCE:
            score, breakdown = calculate_event_importance(
                EventImportanceEvidence(**row) for row in payload.get("evidence", [])
            )
            return {"importance_score": score, "breakdown": breakdown}

        if target == ExperimentTarget.EVENT_AGGREGATION:
            store = event_store or ExperimentEventStore(
                payload.get("state")
                or case.initial_state
                or {"candidates": payload.get("candidates", [])}
            )
            try:
                actual = await store.process(
                    dict(payload.get("message") or payload.get("evidence") or {}),
                    assembly=assembly,
                    client=self._client_factory(payload),
                )
                actual["final_state"] = store.snapshot()
                return _with_metadata(actual, assembly)
            finally:
                if event_store is None:
                    store.close()

        if target == ExperimentTarget.FEATURED_SELECTION:
            candidates = [_featured(value) for value in _candidate_rows(case)]
            plan = assembly.select_featured(candidates)
            return _with_metadata({"featured_plan": plan.model_dump(mode="json")}, assembly)

        if target == ExperimentTarget.DAILY_REPORT:
            candidates = [_daily(value) for value in _candidate_rows(case)]
            plan = assembly.plan_daily_report(candidates)
            return _with_metadata({"daily_plan": plan.model_dump(mode="json")}, assembly)

        raise FixtureExecutionError(f"unsupported experiment target: {target}")

    async def _execute_end_to_end(self, *, payload, assembly):
        store = ExperimentEventStore(payload.get("initial_state"))
        try:
            return await self._execute_message_to_daily(
                payload=payload, assembly=assembly, store=store
            )
        finally:
            store.close()

    async def _execute_message_to_daily(
        self, *, payload: dict[str, Any], assembly: MethodAssembly, store: ExperimentEventStore
    ) -> dict[str, object]:
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise FixtureExecutionError("end_to_end fixture requires messages")
        message_results: list[dict[str, Any]] = []
        featured_rows: list[FeaturedCandidate] = []
        daily_rows: list[DailyReportCandidate] = []
        for message in messages:
            if not isinstance(message, dict):
                raise FixtureExecutionError("end_to_end messages must be objects")
            message_payload = dict(message)
            if isinstance(message.get("evidence"), dict):
                message_payload["evidence"] = dict(message["evidence"])
            if "raw_item" in message:
                item_state = await store.process_item(
                    message_payload, assembly=assembly, client=self._client_factory(message_payload)
                )
                if item_state.get("outcome") != "preview_completed":
                    message_results.append(
                        {"message_id": message["message_id"], "outcome": item_state.get("outcome")}
                    )
                    continue
                analysis = MessageContentAnalysisResult.model_validate(
                    {
                        key: value
                        for key, value in item_state["message_analysis"].items()
                        if key in MessageContentAnalysisResult.model_fields
                    }
                )
                from app.services.item_processing_context import analysis_content

                calculated = item_state["importance"]
                importance = None
                importance_payload = {"content": analysis_content(item_state["translation"])}
                score = calculated["importance_score"]
                message_payload["source"] = message_payload.get("source", {})
                message["published_at"] = message["raw_item"]["published_at"]
            else:
                analysis = await assembly.invoke(
                    "message_analysis",
                    client=self._client_factory(message_payload),
                    payload=_message_input(message_payload).model_dump(mode="json"),
                )
                importance_payload = {
                    **message_payload,
                    "products": analysis.products,
                    "content_form": analysis.content_form,
                }
                importance_payload["extracted_facts"] = {
                    **dict(message.get("extracted_facts") or {}),
                    **analysis.model_dump(mode="json"),
                }
                importance = await assembly.invoke(
                    "importance_scoring",
                    client=self._client_factory(message_payload),
                    payload=_importance_input(importance_payload).model_dump(mode="json"),
                )
                calculated = assembly.calculate_importance(
                    result=importance,
                    content_form=analysis.content_form,
                    scoring_content="\n".join(
                        filter(
                            None, [analysis.title, _importance_input(importance_payload).content]
                        )
                    ),
                )
                score = calculated["importance_score"]
            event_message = {
                **message_payload.get("evidence", {}),
                **analysis.model_dump(mode="json"),
                "source": message_payload.get("source", {}),
                "content": _importance_input(importance_payload).content,
                "message_id": message["message_id"],
                "published_at": message["published_at"],
                "message_type": calculated["message_type"],
                "topics": calculated["topics"],
                "importance_score": score,
                "importance_calculation": calculated["calculation"],
            }
            event = await store.process(
                event_message, assembly=assembly, client=self._client_factory(message_payload)
            )
            message_id = int(message["message_id"])
            published_at = str(message["published_at"])
            featured_rows.append(
                FeaturedCandidate(
                    normalized_item_id=message_id,
                    importance_score=score,
                    content_form=str(analysis.content_form),
                )
            )
            daily_rows.append(
                DailyReportCandidate(
                    message_id=message_id,
                    importance_score=score,
                    published_at=datetime.fromisoformat(published_at),
                    content_form=str(analysis.content_form),
                    products=tuple(str(value) for value in analysis.products),
                    event_ids=tuple(event["event_ids"]),
                )
            )
            message_results.append(
                {
                    "message_id": message_id,
                    "message_analysis": analysis.model_dump(mode="json"),
                    "importance": importance.model_dump(mode="json")
                    if importance is not None
                    else calculated,
                    "importance_score": score,
                    "event_decision": event["event_decision"],
                }
            )
        featured = assembly.select_featured(featured_rows)
        daily = assembly.plan_daily_report(daily_rows)
        return _with_metadata(
            {
                "message_results": message_results,
                "featured_plan": featured.model_dump(mode="json"),
                "daily_plan": daily.model_dump(mode="json"),
                "event_state": store.snapshot(),
            },
            assembly,
            extra_metadata={
                "upstream_mode": "self",
                "entry_stage": "raw_item"
                if all("raw_item" in m for m in messages)
                else "message_analysis",
                "complete_flow": all("raw_item" in m for m in messages),
            },
        )

    async def execute_scenario(self, *, case, candidate, context):
        store = ExperimentEventStore(
            case.initial_state,
            visible_at=case.visible_until
            or (case.steps[0].visible_until or case.steps[0].received_at if case.steps else None),
        )
        try:
            return await self._execute_scenario(
                case=case, candidate=candidate, context=context, store=store
            )
        finally:
            store.close()

    async def _execute_scenario(
        self,
        *,
        case: ExperimentCase,
        candidate: CandidateSpec,
        context: ExperimentExecutionContext,
        store: ExperimentEventStore,
    ) -> dict[str, object]:
        state = dict(case.initial_state)
        results: list[dict[str, Any]] = []
        total_calls = 0
        total_cost = 0.0
        method_calls: list[dict[str, Any]] = []
        online_business_writes = 0
        for index, step in enumerate(case.steps):
            step_input = {**step.input, "state": state}
            message = dict(step_input.get("message") or step_input.get("evidence") or {})
            if not message.get("published_at"):
                message["received_at"] = step.received_at or step.occurred_at
            step_input["message"] = message
            step_case = case.model_copy(
                update={"input": step_input, "steps": [], "initial_state": state}
            )
            try:
                result = await self.execute(
                    case=step_case,
                    candidate=candidate,
                    context=context,
                    event_store=store,
                )
            except Exception as exc:
                results.append(
                    {
                        "step_id": step.step_id,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    }
                )
                results.extend(
                    {
                        "step_id": later.step_id,
                        "status": "invalid",
                        "invalid_reason": f"depends on failed step {step.step_id}",
                    }
                    for later in case.steps[index + 1 :]
                )
                return {
                    "steps": results,
                    "_experiment_metadata": {
                        "valid": False,
                        "call_count": total_calls,
                        "cost_usd": total_cost,
                        "state_isolation_key": context.state_isolation_key,
                    },
                }
            metadata = result.pop("_experiment_metadata", {})
            total_calls += int(metadata.get("call_count") or 0)
            total_cost += float(metadata.get("cost_usd") or 0)
            method_calls.extend(metadata.get("method_calls") or [])
            online_business_writes += int(metadata.get("online_business_writes") or 0)
            results.append({"step_id": step.step_id, "status": "succeeded", "actual": result})
            state = {
                **state,
                **store.snapshot(),
                "processed_step_ids": [
                    *list(state.get("processed_step_ids") or []),
                    step.step_id,
                ],
                "previous_output": result,
            }
        return {
            "steps": results,
            "final_state": state,
            "_experiment_metadata": {
                "valid": True,
                "call_count": total_calls,
                "cost_usd": total_cost,
                "method_calls": method_calls,
                "online_business_writes": online_business_writes,
                "state_isolation_key": context.state_isolation_key,
            },
        }


def _candidate_config(
    default: MethodAssemblyConfig, candidate: CandidateSpec
) -> MethodAssemblyConfig:
    payload = candidate.parameters.get("method_config")
    return MethodAssemblyConfig.model_validate(payload) if isinstance(payload, dict) else default


def _message_input(payload: dict[str, Any]) -> MessageAnalysisInput:
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else payload
    return MessageAnalysisInput(
        title=str(evidence.get("title") or ""),
        content=str(evidence.get("text") or evidence.get("content") or ""),
        evidence_structure=dict(evidence.get("evidence_structure") or {}),
        source_context=dict(evidence.get("source_context") or {}),
        knowledge_rules=tuple(str(value) for value in evidence.get("knowledge_rules") or []),
    )


def _importance_input(payload: dict[str, Any]) -> ImportanceScoringInput:
    return ImportanceScoringInput(
        content=str(payload.get("content") or payload.get("evidence", {}).get("text") or ""),
        extracted_facts=dict(payload.get("extracted_facts") or {}),
        products=tuple(str(value) for value in payload.get("products") or []),
        content_form=str(payload.get("content_form") or "original"),
        source_context=dict(payload.get("source_context") or {}),
        knowledge_rules=tuple(str(value) for value in payload.get("knowledge_rules") or []),
    )


def _event_input(payload: dict[str, Any]) -> EventAggregationInput:
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    candidates = payload.get("candidates") or state.get("candidates") or []
    return EventAggregationInput(
        message=dict(payload.get("message") or payload.get("evidence") or {}),
        possible_event_families=tuple(
            str(value) for value in payload.get("possible_event_families") or []
        ),
        candidates=tuple(dict(value) for value in candidates if isinstance(value, dict)),
    )


def _candidate_rows(case: ExperimentCase) -> list[dict[str, Any]]:
    rows = case.candidates or case.input.get("candidates") or []
    if not isinstance(rows, list):
        raise FixtureExecutionError("candidate collection must be a list")
    return [dict(value) for value in rows if isinstance(value, dict)]


def _featured(value: dict[str, Any]) -> FeaturedCandidate:
    return FeaturedCandidate(
        normalized_item_id=int(value["message_id"]),
        importance_score=float(value["importance_score"]),
        content_form=str(value.get("content_form") or "original"),
    )


def _daily(value: dict[str, Any]) -> DailyReportCandidate:
    published_at = datetime.fromisoformat(str(value["published_at"]))
    return DailyReportCandidate(
        message_id=int(value["message_id"]),
        importance_score=float(value["importance_score"]),
        published_at=published_at,
        content_form=str(value.get("content_form") or "original"),
        products=tuple(str(item) for item in value.get("products") or []),
        event_ids=tuple(int(item) for item in value.get("event_ids") or []),
    )


def _with_metadata(
    actual: dict[str, Any],
    assembly: MethodAssembly,
    *,
    artifact_scope: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, object]:
    metadata: dict[str, Any] = {
        "call_count": len(assembly.calls),
        "method_calls": [call.model_dump(mode="json") for call in assembly.calls],
        "online_business_writes": 0,
    }
    if artifact_scope is not None:
        metadata["artifact_scope"] = artifact_scope
    metadata.update(extra_metadata or {})
    return {**actual, "_experiment_metadata": metadata}
