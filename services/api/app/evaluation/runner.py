from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.domain.message_taxonomy import content_analysis_error


SUPPORTED_TASKS = {
    "relevance",
    "image_ocr",
    "translation",
    "message_analysis",
    "importance",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("task") not in SUPPORTED_TASKS:
            raise ValueError(f"{path}:{line_number}: unsupported task")
        if not row.get("case_id") or "expected" not in row:
            raise ValueError(f"{path}:{line_number}: case_id and expected are required")
        if row["task"] == "message_analysis":
            _validate_message_analysis(row, path=path, line_number=line_number)
        rows.append(row)
    return rows


def _validate_message_analysis(row: dict[str, Any], *, path: Path, line_number: int) -> None:
    expected = row["expected"]
    if not isinstance(expected, dict):
        raise ValueError(f"{path}:{line_number}: message_analysis expected must be an object")
    error = content_analysis_error(
        products=list(expected.get("products") or []),
        content_form=str(expected.get("content_form") or ""),
    )
    if error:
        raise ValueError(f"{path}:{line_number}: {error}")


def compare(
    dataset: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    predictions_by_id = {row["case_id"]: row for row in predictions}
    totals: dict[str, int] = defaultdict(int)
    matches: dict[str, int] = defaultdict(int)
    errors: list[dict[str, Any]] = []
    for case in dataset:
        task = case["task"]
        totals[task] += 1
        prediction = predictions_by_id.get(case["case_id"])
        actual = prediction.get("actual") if prediction else None
        if actual == case["expected"]:
            matches[task] += 1
            continue
        errors.append(
            {
                "case_id": case["case_id"],
                "task": task,
                "expected": case["expected"],
                "actual": actual,
                "notes": case.get("notes"),
            }
        )
    total = sum(totals.values())
    matched = sum(matches.values())
    return {
        "dataset_version": dataset[0].get("dataset_version") if dataset else None,
        "candidate": predictions[0].get("candidate") if predictions else None,
        "total": total,
        "matched": matched,
        "exact_match": matched / total if total else 0,
        "by_task": {
            task: {
                "total": totals[task],
                "matched": matches[task],
                "exact_match": matches[task] / totals[task],
            }
            for task in sorted(totals)
        },
        "errors": errors,
    }


def markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# Message processing evaluation",
        "",
        f"- Dataset: `{result.get('dataset_version')}`",
        f"- Candidate: `{result.get('candidate')}`",
        f"- Exact match: {result['matched']}/{result['total']} ({result['exact_match']:.1%})",
        "",
        "## Errors",
        "",
    ]
    if not result["errors"]:
        lines.append("No mismatches.")
    for error in result["errors"]:
        lines.extend(
            [
                f"### {error['case_id']} · {error['task']}",
                "",
                f"- Expected: `{json.dumps(error['expected'], ensure_ascii=False)}`",
                f"- Actual: `{json.dumps(error['actual'], ensure_ascii=False)}`",
                f"- Notes: {error.get('notes') or '—'}",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(load_jsonl(args.dataset), load_jsonl(args.predictions))
    args.json_output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.markdown_output.write_text(markdown_report(result) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
