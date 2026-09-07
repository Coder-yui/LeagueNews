from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from app.orchestration.experiments.artifacts import LocalExperimentRunStore
from app.orchestration.experiments.contracts import (
    CaseResult,
    CandidateResult,
    CandidateSpec,
    ExperimentCase,
    ExperimentDataset,
    ExperimentExecutionContext,
    ExperimentPlan,
    ExperimentReport,
    ExperimentShape,
    ExperimentTarget,
)


class ExperimentExecutor(Protocol):
    async def execute(
        self,
        *,
        case: ExperimentCase,
        candidate: CandidateSpec,
        context: ExperimentExecutionContext,
    ) -> dict[str, object]: ...


class ScenarioExecutor(Protocol):
    async def execute_scenario(
        self,
        *,
        case: ExperimentCase,
        candidate: CandidateSpec,
        context: ExperimentExecutionContext,
    ) -> dict[str, object]: ...


class ExperimentEvaluator(Protocol):
    def evaluate(
        self,
        *,
        dataset: ExperimentDataset,
        cases: list[CaseResult],
    ) -> dict[str, object]: ...


class ExperimentBudgetExceeded(RuntimeError):
    pass


class _Budget:
    def __init__(self, plan: ExperimentPlan) -> None:
        self.max_total_calls = plan.max_total_calls
        self.max_cost_usd = plan.max_cost_usd
        self.calls = 0
        self.cost_usd = 0.0
        self._lock = asyncio.Lock()

    async def reserve_case(self) -> None:
        async with self._lock:
            if self.max_total_calls is not None and self.calls >= self.max_total_calls:
                raise ExperimentBudgetExceeded("experiment call budget exhausted")
            self.calls += 1

    async def record(self, *, call_count: int, cost_usd: float | None) -> None:
        async with self._lock:
            self.calls += max(call_count - 1, 0)
            self.cost_usd += cost_usd or 0.0
            if self.max_total_calls is not None and self.calls > self.max_total_calls:
                raise ExperimentBudgetExceeded("experiment call budget exhausted")
            if self.max_cost_usd is not None and self.cost_usd > self.max_cost_usd:
                raise ExperimentBudgetExceeded("experiment cost budget exhausted")


class ExperimentRunner:
    def __init__(
        self,
        executors: Mapping[ExperimentTarget, ExperimentExecutor],
        *,
        evaluators: Mapping[ExperimentTarget, ExperimentEvaluator] | None = None,
        state_store: LocalExperimentRunStore | None = None,
    ) -> None:
        self._executors = dict(executors)
        self._evaluators = dict(evaluators or {})
        self._state_store = state_store
        self._state_lock = asyncio.Lock()

    async def run(self, plan: ExperimentPlan) -> ExperimentReport:
        try:
            executor = self._executors[plan.dataset.target]
        except KeyError as exc:
            raise ValueError(
                f"no experiment executor for target: {plan.dataset.target}"
            ) from exc

        plan_key = _fingerprint(plan.model_dump(mode="json"))
        budget = _Budget(plan)
        candidate_results: list[CandidateResult] = []
        for candidate in plan.candidates:
            candidate_results.append(
                await self._run_candidate(
                    plan=plan,
                    plan_key=plan_key,
                    executor=executor,
                    candidate=candidate,
                    budget=budget,
                )
            )

        return ExperimentReport(
            experiment_id=plan.experiment_id,
            dataset_name=plan.dataset.name,
            dataset_version=plan.dataset.version,
            dataset_fingerprint=plan.dataset.fingerprint,
            target=plan.dataset.target,
            candidate_results=candidate_results,
            shape=plan.dataset.shape,
            generated_at=datetime.now(UTC).isoformat(),
            run_metadata={
                "plan_fingerprint": plan_key,
                "cache_enabled": plan.cache_enabled,
                "resume_enabled": plan.resume_enabled,
                "code_version": plan.code_version,
                "prompt_version": plan.prompt_version,
                "model_version": plan.model_version,
                "evaluator_version": plan.evaluator_version,
                "max_concurrency": plan.max_concurrency,
                "case_timeout_seconds": plan.case_timeout_seconds,
                "max_total_calls": plan.max_total_calls,
                "max_cost_usd": plan.max_cost_usd,
                "execution_dataset_fingerprint": plan.dataset.execution_fingerprint,
                "total_calls": budget.calls,
                "total_cost_usd": budget.cost_usd,
            },
        )

    async def _run_candidate(
        self,
        *,
        plan: ExperimentPlan,
        plan_key: str,
        executor: ExperimentExecutor,
        candidate: CandidateSpec,
        budget: _Budget,
    ) -> CandidateResult:
        semaphore = asyncio.Semaphore(plan.max_concurrency)

        async def run_case(case: ExperimentCase) -> CaseResult:
            async with semaphore:
                return await self._run_case(
                    plan=plan,
                    plan_key=plan_key,
                    executor=executor,
                    candidate=candidate,
                    case=case,
                    budget=budget,
                )

        case_results = list(
            await asyncio.gather(*(run_case(case) for case in plan.dataset.cases))
        )
        evaluator = self._evaluators.get(plan.dataset.target)
        metrics = (
            evaluator.evaluate(dataset=plan.dataset, cases=case_results)
            if evaluator is not None
            else {}
        )
        return CandidateResult(
            candidate=candidate,
            succeeded=sum(case.status == "succeeded" for case in case_results),
            failed=sum(case.status != "succeeded" for case in case_results),
            cases=case_results,
            metrics=metrics,
            execution_metadata={
                "cache_hits": sum(case.cache_hit for case in case_results),
                "call_count": sum(case.call_count for case in case_results),
                "manual_review_required": sum(
                    case.manual_review_required for case in case_results
                ),
            },
        )

    async def _run_case(
        self,
        *,
        plan: ExperimentPlan,
        plan_key: str,
        executor: ExperimentExecutor,
        candidate: CandidateSpec,
        case: ExperimentCase,
        budget: _Budget,
    ) -> CaseResult:
        execution_case = case.for_execution()
        input_fingerprint = _fingerprint(_execution_payload(execution_case))
        candidate_fingerprint = _fingerprint(candidate.model_dump(mode="json"))
        cache_key = _fingerprint(
            {
                "dataset": plan.dataset.fingerprint,
                "execution_dataset": plan.dataset.execution_fingerprint,
                "case": input_fingerprint,
                "candidate": candidate_fingerprint,
                "target": plan.dataset.target,
                "code_version": plan.code_version,
                "prompt_version": plan.prompt_version,
                "model_version": plan.model_version,
            }
        )
        if plan.cache_enabled and self._state_store is not None:
            cached = self._state_store.get_cache(cache_key)
            if cached is not None:
                result = CaseResult.model_validate(cached)
                result.cache_hit = True
                return result
        if plan.resume_enabled and self._state_store is not None:
            previous = self._state_store.get_cases(plan_key).get(candidate.candidate_id, {}).get(
                case.case_id
            )
            if isinstance(previous, dict):
                result = CaseResult.model_validate(previous)
                result.cache_hit = True
                return result

        context = ExperimentExecutionContext(
            experiment_id=plan.experiment_id,
            candidate_id=candidate.candidate_id,
            dataset_fingerprint=plan.dataset.fingerprint,
            input_fingerprint=input_fingerprint,
            candidate_fingerprint=candidate_fingerprint,
            cache_key=cache_key,
            state_isolation_key=f"{plan.experiment_id}:{candidate.candidate_id}:{case.case_id}",
            code_version=plan.code_version,
            prompt_version=plan.prompt_version,
            model_version=plan.model_version,
        )
        started = time.perf_counter()
        try:
            await budget.reserve_case()
            async with asyncio.timeout(plan.case_timeout_seconds):
                if (
                    plan.dataset.shape == ExperimentShape.STATEFUL_SCENARIO
                    and hasattr(executor, "execute_scenario")
                ):
                    raw_result = await getattr(executor, "execute_scenario")(
                        case=execution_case,
                        candidate=candidate,
                        context=context,
                    )
                else:
                    raw_result = await executor.execute(
                        case=execution_case,
                        candidate=candidate,
                        context=context,
                    )
            actual, metadata = _split_execution_metadata(raw_result)
            call_count = int(metadata.get("call_count") or 0)
            cost_usd = _optional_float(metadata.get("cost_usd"))
            await budget.record(call_count=call_count, cost_usd=cost_usd)
            result = CaseResult(
                case_id=case.case_id,
                status=("invalid" if metadata.get("valid") is False else "succeeded"),
                actual=actual,
                metadata={
                    **metadata,
                    "cache_key": cache_key,
                    "input_fingerprint": input_fingerprint,
                    "candidate_fingerprint": candidate_fingerprint,
                },
                duration_ms=(time.perf_counter() - started) * 1000,
                call_count=call_count,
                token_count=_optional_int(metadata.get("token_count")),
                cost_usd=cost_usd,
                manual_review_required=bool(metadata.get("manual_review_required")),
            )
        except Exception as exc:
            result = CaseResult(
                case_id=case.case_id,
                status="failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
                metadata={"cache_key": cache_key},
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        if self._state_store is not None:
            payload = result.model_dump(mode="json")
            async with self._state_lock:
                self._state_store.put_case(
                    plan_key,
                    candidate_id=candidate.candidate_id,
                    case_id=case.case_id,
                    result=payload,
                )
                if plan.cache_enabled and result.status == "succeeded":
                    self._state_store.put_cache(cache_key, payload)
        return result


def _execution_payload(case: ExperimentCase) -> dict[str, Any]:
    return {
        "input": case.input,
        "media_artifacts": [artifact.model_dump(mode="json") for artifact in case.media_artifacts],
        "initial_state": case.initial_state,
        "steps": [step.model_dump(mode="json") for step in case.steps],
        "candidates": case.candidates,
        "occurred_at": case.occurred_at,
        "received_at": case.received_at,
        "visible_until": case.visible_until,
    }


def _split_execution_metadata(value: dict[str, object]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(value, dict):
        raise TypeError("experiment executor must return an object")
    actual = dict(value)
    metadata = actual.pop("_experiment_metadata", {})
    if not isinstance(metadata, dict):
        raise TypeError("_experiment_metadata must be an object")
    return actual, metadata


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
