"""Run a frozen-dataset experiment with an explicit provider selection.

The experiment never connects to the application database. A
plan may embed a dataset or refer to an exported ``manifest.json`` next to the
plan, and all execution is routed through the Phase 3 frozen executor.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import hashlib
from pathlib import Path

from app.orchestration.experiments import (
    ExperimentPlan,
    ExperimentRunner,
    FrozenExperimentExecutor,
    LocalExperimentArtifactStore,
    LocalExperimentRunStore,
    default_evaluators,
    load_frozen_dataset,
)


def load_plan(path: Path) -> ExperimentPlan:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    manifest = payload.pop("dataset_manifest", None)
    if manifest is not None:
        manifest_path = (path.parent / str(manifest)).resolve()
        payload["dataset"] = load_frozen_dataset(manifest_path).model_dump(mode="json")
    return ExperimentPlan.model_validate(payload)


async def run(args: argparse.Namespace) -> Path:
    plan = load_plan(args.plan)
    if args.max_concurrency is not None:
        plan = plan.model_copy(update={"max_concurrency": args.max_concurrency})
    if args.no_cache:
        plan = plan.model_copy(update={"cache_enabled": False})
    if args.no_resume:
        plan = plan.model_copy(update={"resume_enabled": False})

    # Cache identity includes uncommitted code and provider mode, not just a user label.
    code = hashlib.sha256()
    for path in sorted((Path(__file__).resolve().parents[1] / "app").rglob("*.py")):
        code.update(str(path.relative_to(Path(__file__).resolve().parents[1])).encode())
        code.update(path.read_bytes())
    plan = plan.model_copy(
        update={
            "code_version": code.hexdigest(),
            "evaluator_version": "frozen-v2:" + code.hexdigest(),
            "model_version": args.provider + ":" + plan.model_version,
        }
    )
    request_count = 0
    request_lock = asyncio.Lock()

    async def before_request():
        nonlocal request_count
        async with request_lock:
            if plan.max_total_calls is not None and request_count >= plan.max_total_calls:
                raise RuntimeError("actual provider request budget exhausted")
            request_count += 1

    client_factory = None
    if args.provider == "configured":
        from app.services.llm import LLMClient
        from app.core.config import settings
        from app.methods import MethodAssemblyConfig

        if plan.max_cost_usd is not None:
            raise ValueError(
                "USD budgets require provider pricing; use max_total_calls for configured experiments"
            )
        candidates = []
        for candidate in plan.candidates:
            config = MethodAssemblyConfig.model_validate(
                candidate.parameters.get("method_config", {})
            )
            parameters = dict(config.model_parameters)
            for method in ("message_analysis", "importance_scoring", "event_aggregation"):
                if config.implementation_for(method) == "baseline":
                    parameters[method] = {
                        "model": settings.model_name,
                        "temperature": 0.1,
                        **parameters.get(method, {}),
                    }
            config = config.model_copy(update={"model_parameters": parameters})
            candidates.append(
                candidate.model_copy(
                    update={
                        "parameters": {
                            **candidate.parameters,
                            "method_config": config.model_dump(mode="json"),
                        }
                    }
                )
            )
        endpoint = hashlib.sha256(settings.openai_base_url.encode()).hexdigest()
        plan = plan.model_copy(
            update={"candidates": candidates, "model_version": "configured:" + hashlib.sha256(
                json.dumps({"endpoint": endpoint, "default_model": settings.model_name,
                            "sdk_retries": settings.llm_max_retries,
                            "timeout_seconds": settings.llm_timeout_seconds}, sort_keys=True).encode()
            ).hexdigest()}
        )

        def client_factory(_payload):
            return LLMClient(before_request=before_request)

    executor = FrozenExperimentExecutor(client_factory=client_factory)
    from app.services.call_metering import SQLiteAttemptRecorder

    runner = ExperimentRunner(
        {target: executor for target in {candidate.target for candidate in plan.candidates}},
        evaluators=default_evaluators(),
        attempt_recorder=SQLiteAttemptRecorder(args.artifact_root / ".attempts" / f"{plan.experiment_id}.sqlite3"),
        state_store=(
            None
            if args.no_cache and args.no_resume
            else LocalExperimentRunStore(args.artifact_root / ".runs", plan.experiment_id)
        ),
    )
    report = await runner.run(plan)
    report.run_metadata["provider_mode"] = args.provider
    report.run_metadata["actual_provider_requests"] = request_count
    directory = LocalExperimentArtifactStore(args.artifact_root).write(
        plan=plan,
        report=report,
        overwrite=args.overwrite,
    )
    print(
        json.dumps(
            {
                "experiment_id": report.experiment_id,
                "dataset_fingerprint": report.dataset_fingerprint,
                "artifact_directory": str(directory),
                "candidates": [
                    {
                        "candidate": result.candidate.candidate_id,
                        "succeeded": result.succeeded,
                        "failed": result.failed,
                        "metrics": result.metrics,
                    }
                    for result in report.candidate_results
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return directory


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a LeagueNews frozen experiment")
    parser.add_argument(
        "--provider",
        choices=("fixture", "configured"),
        default="fixture",
        help="fixture is offline; configured makes actual model requests using configured credentials",
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--max-concurrency", type=int)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.max_concurrency is not None and not 1 <= args.max_concurrency <= 32:
        parser.error("--max-concurrency must be between 1 and 32")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
