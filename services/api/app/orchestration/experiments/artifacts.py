import json
from pathlib import Path

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
        return directory

    @staticmethod
    def _write_json(path: Path, payload: dict[str, object]) -> None:
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
