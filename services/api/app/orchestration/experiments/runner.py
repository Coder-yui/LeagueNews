import asyncio
from collections.abc import Mapping
from typing import Protocol

from app.orchestration.experiments.contracts import (
    CandidateResult,
    CandidateSpec,
    CaseResult,
    ExperimentCase,
    ExperimentDataset,
    ExperimentExecutionContext,
    ExperimentPlan,
    ExperimentReport,
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


class ExperimentEvaluator(Protocol):
    def evaluate(
        self,
        *,
        dataset: ExperimentDataset,
        cases: list[CaseResult],
    ) -> dict[str, object]: ...


class ExactMatchEvaluator:
    """Small baseline metric; domain-specific evaluators can replace it per target."""

    def evaluate(
        self,
        *,
        dataset: ExperimentDataset,
        cases: list[CaseResult],
    ) -> dict[str, object]:
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
            "exact_match": (
                matched / len(labeled_results) if labeled_results else None
            ),
        }


class ExperimentRunner:
    def __init__(
        self,
        executors: Mapping[ExperimentTarget, ExperimentExecutor],
        *,
        evaluators: Mapping[ExperimentTarget, ExperimentEvaluator] | None = None,
    ) -> None:
        self._executors = dict(executors)
        self._evaluators = dict(evaluators or {})

    async def run(self, plan: ExperimentPlan) -> ExperimentReport:
        try:
            executor = self._executors[plan.dataset.target]
        except KeyError as exc:
            raise ValueError(
                f"no experiment executor for target: {plan.dataset.target}"
            ) from exc

        candidate_results: list[CandidateResult] = []
        for candidate in plan.candidates:
            semaphore = asyncio.Semaphore(plan.max_concurrency)
            context = ExperimentExecutionContext(
                experiment_id=plan.experiment_id,
                candidate_id=candidate.candidate_id,
                dataset_fingerprint=plan.dataset.fingerprint,
            )

            async def run_case(case: ExperimentCase) -> CaseResult:
                async with semaphore:
                    try:
                        async with asyncio.timeout(plan.case_timeout_seconds):
                            actual = await executor.execute(
                                case=case,
                                candidate=candidate,
                                context=context,
                            )
                    except Exception as exc:
                        return CaseResult(
                            case_id=case.case_id,
                            status="failed",
                            error_type=type(exc).__name__,
                            error_message=str(exc),
                        )
                    return CaseResult(
                        case_id=case.case_id,
                        status="succeeded",
                        actual=actual,
                    )

            cases = await asyncio.gather(
                *(run_case(case) for case in plan.dataset.cases)
            )
            succeeded = sum(case.status == "succeeded" for case in cases)
            evaluator = self._evaluators.get(plan.dataset.target)
            metrics = (
                evaluator.evaluate(dataset=plan.dataset, cases=list(cases))
                if evaluator is not None
                else {}
            )
            candidate_results.append(
                CandidateResult(
                    candidate=candidate,
                    succeeded=succeeded,
                    failed=len(cases) - succeeded,
                    cases=list(cases),
                    metrics=metrics,
                )
            )

        return ExperimentReport(
            experiment_id=plan.experiment_id,
            dataset_name=plan.dataset.name,
            dataset_version=plan.dataset.version,
            dataset_fingerprint=plan.dataset.fingerprint,
            target=plan.dataset.target,
            candidate_results=candidate_results,
        )
