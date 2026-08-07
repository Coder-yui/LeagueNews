from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.domain.ontology import (
    AGGREGATION_STRATEGIES,
    CONTENT_FORMS,
    EVENT_KINDS,
    INFORMATION_STAGES,
    PRIMARY_TOPICS,
    PRODUCT_SCOPES,
    SOURCE_KINDS,
    SUBTOPICS,
)


SUPPORTED_TASKS = {
    "relevance",
    "translation",
    "entities",
    "classification",
    "importance",
    "event_candidate",
    "event_decision",
    "ontology_v2",
}

_CONTROLLED_EXPECTED_FIELDS = {
    "source_kind": SOURCE_KINDS,
    "information_stage": INFORMATION_STAGES,
    "content_form": CONTENT_FORMS,
    "primary_topic": PRIMARY_TOPICS,
    "subtopic": SUBTOPICS,
    "product_scope": PRODUCT_SCOPES,
    "event_kind": EVENT_KINDS,
    "aggregation_strategy": AGGREGATION_STRATEGIES,
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
        if row.get("task") == "ontology_v2":
            _validate_ontology_case(row, path=path, line_number=line_number)
        rows.append(row)
    return rows


def _validate_ontology_case(
    row: dict[str, Any], *, path: Path, line_number: int
) -> None:
    expected = row.get("expected")
    input_data = row.get("input")
    if not isinstance(expected, dict) or not isinstance(input_data, dict):
        raise ValueError(f"{path}:{line_number}: ontology_v2 requires object input/expected")
    if not isinstance(input_data.get("raw_item_id"), int):
        raise ValueError(f"{path}:{line_number}: raw_item_id is required")
    if input_data.get("is_revision_head") is not True:
        raise ValueError(f"{path}:{line_number}: only revision heads may be evaluated")
    tags = row.get("coverage_tags")
    if not isinstance(tags, list) or not tags:
        raise ValueError(f"{path}:{line_number}: coverage_tags are required")
    for field, allowed in _CONTROLLED_EXPECTED_FIELDS.items():
        value = expected.get(field)
        if value is not None and value not in allowed:
            raise ValueError(
                f"{path}:{line_number}: invalid {field}={value!r}"
            )
    band = expected.get("importance_band")
    if band is not None and (
        not isinstance(band, list)
        or len(band) != 2
        or not all(isinstance(value, (int, float)) for value in band)
        or not 0 <= band[0] <= band[1] <= 1
    ):
        raise ValueError(f"{path}:{line_number}: invalid importance_band")
    prefixes = expected.get("route_key_prefixes", [])
    if not isinstance(prefixes, list) or not all(
        isinstance(value, str) and value and "unknown" not in value.casefold()
        for value in prefixes
    ):
        raise ValueError(f"{path}:{line_number}: invalid route_key_prefixes")
    chain_fields = (
        input_data.get("chain_id"),
        input_data.get("chain_stage"),
        input_data.get("chain_order"),
        expected.get("event_group"),
    )
    if any(value is not None for value in chain_fields):
        if (
            not all(value is not None for value in chain_fields)
            or not all(isinstance(value, str) and value for value in chain_fields[:2])
            or not isinstance(chain_fields[2], int)
            or not isinstance(chain_fields[3], str)
        ):
            raise ValueError(f"{path}:{line_number}: incomplete chain metadata")


def _event_identities(actual: object) -> set[str]:
    if not isinstance(actual, dict):
        return set()
    event_ids = actual.get("event_ids")
    if isinstance(event_ids, list):
        return {f"event:{value}" for value in event_ids if isinstance(value, int)}
    route_keys = actual.get("route_keys")
    if isinstance(route_keys, list):
        return {f"key:{value}" for value in route_keys if isinstance(value, str)}
    return set()


def _ontology_match(
    expected: dict[str, Any], actual: object
) -> tuple[bool, list[str]]:
    if not isinstance(actual, dict):
        return False, ["missing ontology output"]
    mismatches: list[str] = []
    for field, expected_value in expected.items():
        if field in {"importance_band", "route_key_prefixes", "event_group"}:
            continue
        if actual.get(field) != expected_value:
            mismatches.append(field)
    band = expected.get("importance_band")
    if isinstance(band, list):
        score = actual.get("importance_score")
        if not isinstance(score, (int, float)) or not band[0] <= score <= band[1]:
            mismatches.append("importance_score")
    prefixes = expected.get("route_key_prefixes")
    if isinstance(prefixes, list):
        actual_keys = actual.get("route_keys")
        if not isinstance(actual_keys, list) or len(actual_keys) != len(prefixes):
            mismatches.append("route_keys")
        elif any(
            not isinstance(key, str) or not key.startswith(prefix)
            for key, prefix in zip(actual_keys, prefixes, strict=True)
        ):
            mismatches.append("route_keys")
    actual_keys = actual.get("route_keys", [])
    if isinstance(actual_keys, list) and any(
        isinstance(key, str) and "unknown" in key.casefold() for key in actual_keys
    ):
        mismatches.append("unknown_route_key")
    for field, allowed in _CONTROLLED_EXPECTED_FIELDS.items():
        value = actual.get(field)
        if value is not None and value not in allowed:
            mismatches.append(f"invalid_{field}")
    return not mismatches, sorted(set(mismatches))


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
    ontology_mismatch_counts: dict[str, int] = defaultdict(int)
    chain_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in dataset:
        task = case["task"]
        task_totals[task] += 1
        prediction = by_id.get(case["case_id"])
        actual = prediction.get("actual") if prediction else None
        expected = case["expected"]
        event_group = (
            expected.get("event_group") if isinstance(expected, dict) else None
        )
        if isinstance(event_group, str):
            chain_groups[event_group].append(
                {
                    "case_id": case["case_id"],
                    "chain_id": case["input"]["chain_id"],
                    "stage": case["input"]["chain_stage"],
                    "order": case["input"]["chain_order"],
                    "identities": _event_identities(actual),
                }
            )
        if task == "ontology_v2":
            matched, ontology_mismatches = _ontology_match(case["expected"], actual)
            for mismatch in ontology_mismatches:
                ontology_mismatch_counts[mismatch] += 1
        else:
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
    chain_results = []
    for event_group, members in sorted(chain_groups.items()):
        ordered_members = sorted(members, key=lambda member: member["order"])
        identity_sets = [member["identities"] for member in ordered_members]
        shared = set.intersection(*identity_sets) if identity_sets else set()
        chain_results.append(
            {
                "event_group": event_group,
                "chain_id": ordered_members[0]["chain_id"],
                "cases": [member["case_id"] for member in ordered_members],
                "stages": [member["stage"] for member in ordered_members],
                "shared_event_identities": sorted(shared),
                "fully_aggregated": bool(shared),
            }
        )
    false_merge_pairs = []
    identities_by_group = {
        result["event_group"]: set(result["shared_event_identities"])
        for result in chain_results
    }
    groups = sorted(identities_by_group)
    for index, left in enumerate(groups):
        for right in groups[index + 1 :]:
            overlap = identities_by_group[left] & identities_by_group[right]
            if overlap:
                false_merge_pairs.append(
                    {"left": left, "right": right, "identities": sorted(overlap)}
                )
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
        "ontology_invariants": dict(sorted(ontology_mismatch_counts.items())),
        "event_chains": {
            "total": len(chain_results),
            "fully_aggregated": sum(
                result["fully_aggregated"] for result in chain_results
            ),
            "false_merge_pairs": false_merge_pairs,
            "groups": chain_results,
        },
        "coverage": dict(
            sorted(
                {
                    tag: sum(tag in case.get("coverage_tags", []) for case in dataset)
                    for case in dataset
                    for tag in case.get("coverage_tags", [])
                }.items()
            )
        ),
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
