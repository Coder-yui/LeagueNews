from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_FIXTURE = Path(__file__).parents[1] / "evals" / "event_aggregation_v2_cases.json"


def _match_memberships(
    expected: list[dict[str, Any]], predicted: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Match action/key exactly and treat an omitted expected relation as a wildcard."""

    unmatched_predictions = [dict(row) for row in predicted]
    missing: list[dict[str, Any]] = []
    for expected_row in expected:
        match_index = next(
            (
                index
                for index, predicted_row in enumerate(unmatched_predictions)
                if predicted_row.get("action") == expected_row.get("action")
                and predicted_row.get("event_key") == expected_row.get("event_key")
                and (
                    not expected_row.get("event_family")
                    or predicted_row.get("event_family")
                    == expected_row.get("event_family")
                )
                and (
                    not expected_row.get("relation")
                    or predicted_row.get("relation") == expected_row.get("relation")
                )
            ),
            None,
        )
        if match_index is None:
            missing.append(expected_row)
        else:
            unmatched_predictions.pop(match_index)
    return missing, unmatched_predictions


def evaluate(fixture: dict[str, Any], predictions: dict[str, Any]) -> dict[str, Any]:
    expected_by_item = {
        int(item["normalized_item_id"]): item.get("expected", [])
        for case in fixture["cases"]
        for item in case["items"]
    }
    predicted_by_item = {
        int(item["normalized_item_id"]): item.get("mentions", [])
        for item in predictions.get("items", [])
    }
    counts: Counter[str] = Counter()
    details: list[dict[str, Any]] = []
    for item_id, expected in expected_by_item.items():
        predicted = predicted_by_item.get(item_id, [])
        missing, extra = _match_memberships(expected, predicted)
        if not missing and not extra:
            counts["exact"] += 1
            outcome = "exact"
        else:
            counts["mismatch"] += 1
            counts["missed_membership"] += len(missing)
            counts["extra_membership"] += len(extra)
            outcome = "mismatch"
        details.append(
            {
                "normalized_item_id": item_id,
                "outcome": outcome,
                "missing": missing,
                "extra": extra,
            }
        )
    counts["missing_prediction"] = len(set(expected_by_item) - set(predicted_by_item))
    return {
        "fixture_version": fixture["version"],
        "item_count": len(expected_by_item),
        "counts": dict(counts),
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score offline Event membership predictions against the V2 fixture."
    )
    parser.add_argument("predictions", type=Path, nargs="?")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text())
    if args.predictions is None:
        item_count = sum(len(case["items"]) for case in fixture["cases"])
        print(
            json.dumps(
                {
                    "fixture_version": fixture["version"],
                    "case_count": len(fixture["cases"]),
                    "item_count": item_count,
                    "categories": dict(Counter(case["category"] for case in fixture["cases"])),
                    "status": "fixture_validated; pass a predictions JSON file to score a model run",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    predictions = json.loads(args.predictions.read_text())
    print(json.dumps(evaluate(fixture, predictions), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
