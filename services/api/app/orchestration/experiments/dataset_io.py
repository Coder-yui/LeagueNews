"""Import/export and governance checks for frozen experiment datasets.

The on-disk format intentionally contains the evidence needed to execute a
case.  A database identifier may be retained as provenance inside the input,
but it is never sufficient to make a case executable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.orchestration.experiments.contracts import (
    ExperimentCase,
    ExperimentDataset,
    FrozenMediaArtifact,
    LabelSource,
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def load_frozen_dataset(manifest_path: Path, cases_path: Path | None = None) -> ExperimentDataset:
    """Load and validate a manifest plus JSONL case file."""

    manifest = _read_json(manifest_path)
    resolved_cases = cases_path or manifest_path.with_name(str(manifest.get("cases_file", "cases.jsonl")))
    rows: list[ExperimentCase] = []
    for line_number, line in enumerate(resolved_cases.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            rows.append(ExperimentCase.model_validate(value))
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ValueError(f"{resolved_cases}:{line_number}: invalid case: {exc}") from exc

    dataset_payload = {
        key: value
        for key, value in manifest.items()
        if key not in {"cases_file", "fingerprint", "case_count"}
    }
    dataset_payload["cases"] = [case.model_dump(mode="json") for case in rows]
    dataset = ExperimentDataset.model_validate(dataset_payload)
    _validate_frozen_inputs(dataset, manifest_path=manifest_path)
    expected_fingerprint = manifest.get("fingerprint")
    if expected_fingerprint and expected_fingerprint != dataset.fingerprint:
        raise ValueError(
            f"{manifest_path}: fingerprint mismatch; expected {expected_fingerprint}, "
            f"computed {dataset.fingerprint}"
        )
    if manifest.get("case_count") is not None and manifest["case_count"] != len(rows):
        raise ValueError(f"{manifest_path}: case_count does not match cases.jsonl")
    return dataset


def _validate_frozen_inputs(dataset: ExperimentDataset, *, manifest_path: Path) -> None:
    splits_by_group: dict[str, set[str]] = {}
    for case in dataset.cases:
        if case.label_source not in set(LabelSource):
            raise ValueError(f"{manifest_path}: unsupported label source for {case.case_id}")
        if case.label_source == LabelSource.HUMAN_CONFIRMED and not case.label_values:
            raise ValueError(f"{manifest_path}: human label is empty for {case.case_id}")
        if case.label_source == LabelSource.UNLABELED and case.label_values:
            raise ValueError(f"{manifest_path}: unlabeled case contains labels: {case.case_id}")
        for artifact in case.media_artifacts:
            FrozenMediaArtifact.model_validate(artifact)
        if _only_online_identifiers(case.input) and not (
            case.candidates or case.steps or case.media_artifacts
        ):
            raise ValueError(
                f"{manifest_path}: {case.case_id} contains only online identifiers; "
                "freeze evidence/context before execution"
            )
        if case.group_id is not None:
            splits_by_group.setdefault(case.group_id, set()).add(case.split)
    leaked_groups = sorted(
        group_id for group_id, splits in splits_by_group.items() if len(splits) > 1
    )
    if leaked_groups:
        raise ValueError(
            f"{manifest_path}: group_id crosses data splits: {', '.join(leaked_groups)}"
        )


def _only_online_identifiers(value: dict[str, Any]) -> bool:
    if not value:
        return True
    keys = set(value)
    identifier_keys = {key for key in keys if key.endswith("_id") or key in {"id", "ids"}}
    evidence_keys = {
        "evidence",
        "content",
        "content_blocks",
        "title",
        "message",
        "candidates",
        "steps",
        "fixture_outputs",
        "mock_responses",
    }
    return bool(identifier_keys) and not keys.intersection(evidence_keys)


def export_frozen_dataset(dataset: ExperimentDataset, root: Path, *, overwrite: bool = False) -> tuple[Path, Path]:
    """Write a versioned manifest and JSONL cases atomically enough for local use."""

    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    cases_path = root / "cases.jsonl"
    if not overwrite and (manifest_path.exists() or cases_path.exists()):
        raise FileExistsError(f"dataset files already exist under {root}")
    cases_text = "".join(
        json.dumps(case.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n"
        for case in dataset.cases
    )
    cases_tmp = cases_path.with_suffix(".jsonl.tmp")
    manifest_tmp = manifest_path.with_suffix(".json.tmp")
    cases_tmp.write_text(cases_text, encoding="utf-8")
    manifest_tmp.write_text(
        json.dumps(
            {
                **dataset.model_dump(exclude={"cases"}, mode="json"),
                "cases_file": cases_path.name,
                "case_count": len(dataset.cases),
                "fingerprint": dataset.fingerprint,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    cases_tmp.replace(cases_path)
    manifest_tmp.replace(manifest_path)
    return manifest_path, cases_path
