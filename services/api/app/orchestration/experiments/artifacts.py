import json
from pathlib import Path
from typing import Any

from app.orchestration.experiments.contracts import ExperimentPlan, ExperimentReport


class LocalExperimentArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def write(
        self,
        *,
        plan: ExperimentPlan,
        report: ExperimentReport,
        overwrite: bool = False,
    ) -> Path:
        if plan.experiment_id != report.experiment_id:
            raise ValueError("plan and report belong to different experiments")
        directory = self.root / plan.experiment_id
        if directory.exists() and not overwrite:
            raise FileExistsError(f"experiment artifact already exists: {directory}")
        directory.mkdir(parents=True, exist_ok=True)
        self._write_json(directory / "plan.json", plan.model_dump(mode="json"))
        self._write_json(directory / "report.json", report.model_dump(mode="json"))
        self._write_json(directory / "comparison.json", comparison_payload(report))
        (directory / "comparison.html").write_text(
            comparison_html(report), encoding="utf-8"
        )
        (directory / "results.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        "candidate": candidate.candidate.candidate_id,
                        "case": case.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
                for candidate in report.candidate_results
                for case in candidate.cases
            ),
            encoding="utf-8",
        )
        return directory

    @staticmethod
    def _write_json(path: Path, payload: dict[str, object]) -> None:
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


class LocalExperimentRunStore:
    """Small JSON state store for cache entries and per-case checkpoints."""

    def __init__(self, root: Path, experiment_id: str) -> None:
        self.directory = root / experiment_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cache_path = self.directory / "cache.json"
        self.checkpoint_path = self.directory / "checkpoint.json"

    def _read(self, path: Path, default: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return default
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"experiment state must be an object: {path}")
        return value

    def get_cache(self, key: str) -> dict[str, Any] | None:
        return self._read(self.cache_path, {}).get(key)

    def put_cache(self, key: str, value: dict[str, Any]) -> None:
        payload = self._read(self.cache_path, {})
        payload[key] = value
        self._atomic_write(self.cache_path, payload)

    def get_cases(self, plan_key: str) -> dict[str, dict[str, Any]]:
        payload = self._read(self.checkpoint_path, {})
        value = payload.get(plan_key, {})
        return value if isinstance(value, dict) else {}

    def put_case(
        self,
        plan_key: str,
        *,
        candidate_id: str,
        case_id: str,
        result: dict[str, Any],
    ) -> None:
        payload = self._read(self.checkpoint_path, {})
        plan_payload = payload.setdefault(plan_key, {})
        candidate_payload = plan_payload.setdefault(candidate_id, {})
        candidate_payload[case_id] = result
        self._atomic_write(self.checkpoint_path, payload)

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


def comparison_payload(report: ExperimentReport) -> dict[str, Any]:
    cases_by_id: dict[str, list[dict[str, Any]]] = {}
    for candidate in report.candidate_results:
        for case in candidate.cases:
            cases_by_id.setdefault(case.case_id, []).append(
                {
                    "candidate": candidate.candidate.candidate_id,
                    "status": case.status,
                    "actual": case.actual,
                    "error_type": case.error_type,
                    "cache_hit": case.cache_hit,
                    "expected": case.metadata.get("expected"),
                    "label_source": case.metadata.get("label_source"),
                    "measurement": case.metadata,
                    "duration_ms": case.duration_ms,
                }
            )
    regression_cases = []
    if report.candidate_results:
        baseline = report.candidate_results[0]
        baseline_by_id = {case.case_id: case for case in baseline.cases}
        for candidate in report.candidate_results[1:]:
            for case in candidate.cases:
                base = baseline_by_id.get(case.case_id)
                if base is not None and (base.status != case.status or base.actual != case.actual):
                    regression_cases.append(
                        {
                            "candidate": candidate.candidate.candidate_id,
                            "case": case.case_id,
                            "baseline_status": base.status,
                            "candidate_status": case.status,
                        }
                    )
    return {
        "experiment_id": report.experiment_id,
        "target": report.target,
        "shape": report.shape,
        "dataset_fingerprint": report.dataset_fingerprint,
        "candidates": [
            {
                "candidate": result.candidate.candidate_id,
                "succeeded": result.succeeded,
                "failed": result.failed,
                "metrics": result.metrics,
                "execution_metadata": result.execution_metadata,
            }
            for result in report.candidate_results
        ],
        "case_comparison": cases_by_id,
        "regression_cases": regression_cases,
    }


def comparison_html(report: ExperimentReport) -> str:
    rows = []
    for result in report.candidate_results:
        rows.append(
            "<tr>"
            f"<td>{_escape(result.candidate.candidate_id)}</td>"
            f"<td>{result.succeeded}</td>"
            f"<td>{result.failed}</td>"
            f"<td><pre>{_escape(json.dumps(result.metrics, ensure_ascii=False, indent=2))}</pre></td>"
            "</tr>"
        )
    case_rows = []
    comparison = comparison_payload(report)["case_comparison"]
    for case_id, results in comparison.items():
        for result in results:
            case_rows.append(
                "<tr>"
                f"<td>{_escape(case_id)}</td>"
                f"<td>{_escape(str(result['candidate']))}</td>"
                f"<td>{_escape(str(result['status']))}</td>"
                f"<td><pre>{_escape(json.dumps(result, ensure_ascii=False, indent=2))}</pre></td>"
                "</tr>"
            )
    return (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>Experiment {_escape(report.experiment_id)}</title>"
        "<style>body{font-family:system-ui;margin:2rem}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:.5rem;vertical-align:top}pre{margin:0;white-space:pre-wrap}</style>"
        f"<h1>{_escape(report.experiment_id)}</h1>"
        f"<p>target={_escape(str(report.target))} · shape={_escape(str(report.shape))} · "
        f"dataset={_escape(report.dataset_fingerprint)}</p>"
        "<table><thead><tr><th>Candidate</th><th>Succeeded</th><th>Failed/invalid</th>"
        "<th>Metrics</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        "<h2>Case comparison</h2>"
        "<table><thead><tr><th>Case</th><th>Candidate</th><th>Status</th><th>Actual</th></tr></thead><tbody>"
        + "".join(case_rows)
        + "</tbody></table>"
    )


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
