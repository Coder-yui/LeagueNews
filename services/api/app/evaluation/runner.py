from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


SUPPORTED_TASKS = {
    "relevance",
    "translation",
    "entities",
    "classification",
    "importance",
    "event_candidate",
    "event_decision",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("task") not in SUPPORTED_TASKS:
            raise ValueError(f"{path}:{line_number}: unsupported task")
        if not row.get("case_id") or "expected" not in row:
            raise ValueError(f"{path}:{line_number}: case_id and expected are required")
        rows.append(row)
    return rows


def compare(
    dataset: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    by_id = {row["case_id"]: row for row in predictions}
    task_totals: dict[str, int] = defaultdict(int)
    task_matches: dict[str, int] = defaultdict(int)
    errors = []
    event_candidate_total = 0
    event_candidate_hits = 0
    merge_decisions = 0
    false_merges = 0
    split_decisions = 0
    false_splits = 0
    for case in dataset:
        task = case["task"]
        task_totals[task] += 1
        prediction = by_id.get(case["case_id"])
        actual = prediction.get("actual") if prediction else None
        matched = actual == case["expected"]
        if task == "event_candidate" and isinstance(case["expected"], dict):
            relevant_id = case["expected"].get("relevant_event_id")
            candidate_ids = (
                actual.get("candidate_event_ids", [])
                if isinstance(actual, dict)
                else []
            )
            if relevant_id is not None:
                event_candidate_total += 1
                event_candidate_hits += int(relevant_id in candidate_ids[:5])
        if task == "event_decision" and isinstance(actual, dict):
            actual_decision = actual.get("decision")
            expected_decision = (
                case["expected"].get("decision")
                if isinstance(case["expected"], dict)
                else None
            )
            if actual_decision == "update":
                merge_decisions += 1
                false_merges += int(expected_decision != "update")
            if actual_decision == "create":
                split_decisions += 1
                false_splits += int(expected_decision != "create")
        if matched:
            task_matches[task] += 1
        else:
            errors.append(
                {
                    "case_id": case["case_id"],
                    "task": task,
                    "expected": case["expected"],
                    "actual": actual,
                    "notes": case.get("notes"),
                }
            )
    total = sum(task_totals.values())
    matched = sum(task_matches.values())
    return {
        "dataset_version": dataset[0].get("dataset_version") if dataset else None,
        "candidate": predictions[0].get("candidate") if predictions else None,
        "total": total,
        "matched": matched,
        "exact_match": matched / total if total else 0,
        "by_task": {
            task: {
                "total": task_totals[task],
                "matched": task_matches[task],
                "exact_match": task_matches[task] / task_totals[task],
            }
            for task in sorted(task_totals)
        },
        "errors": errors,
        "event_retrieval": {
            "recall_at_5": (
                event_candidate_hits / event_candidate_total
                if event_candidate_total
                else None
            ),
            "evaluated_cases": event_candidate_total,
            "false_merge_rate": (
                false_merges / merge_decisions if merge_decisions else None
            ),
            "false_split_rate": (
                false_splits / split_decisions if split_decisions else None
            ),
        },
    }


def markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# Offline evaluation report",
        "",
        f"- Dataset: `{result.get('dataset_version')}`",
        f"- Candidate: `{result.get('candidate')}`",
        f"- Exact match: {result['matched']}/{result['total']} "
        f"({result['exact_match']:.1%})",
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
