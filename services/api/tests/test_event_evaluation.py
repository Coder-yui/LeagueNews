import json
from pathlib import Path

from scripts.evaluate_event_aggregation import evaluate


FIXTURE = Path(__file__).parents[1] / "evals" / "event_aggregation_v2_cases.json"
LIVE_RESULT = (
    Path(__file__).parents[1]
    / "evals"
    / "event_aggregation_v2_live_2026-08-13.json"
)


def test_real_data_fixture_is_well_formed_and_covers_required_error_types() -> None:
    fixture = json.loads(FIXTURE.read_text())
    items = [item for case in fixture["cases"] for item in case["items"]]
    item_ids = [int(item["normalized_item_id"]) for item in items]
    risks = {risk for case in fixture["cases"] for risk in case["risks"]}

    assert fixture["version"] == "event-aggregation-eval-v2"
    assert len(fixture["cases"]) == 18
    assert len(items) == len(set(item_ids)) == 35
    assert {
        "false_merge",
        "false_split",
        "missed_attach",
        "wrong_attach",
        "unnecessary_create",
        "missed_create",
        "missed_event",
    } <= risks


def test_evaluator_accepts_unspecified_relation_as_wildcard_and_reports_errors() -> None:
    fixture = {
        "version": "test",
        "cases": [
            {
                "items": [
                    {
                        "normalized_item_id": 1,
                        "expected": [
                            {
                                "action": "attach",
                                "event_key": "event-a",
                                "event_family": "gameplay_balance",
                            }
                        ],
                    },
                    {
                        "normalized_item_id": 2,
                        "expected": [
                            {
                                "action": "attach",
                                "event_key": "event-b",
                                "relation": "corrects",
                            }
                        ],
                    },
                ]
            }
        ],
    }
    predictions = {
        "items": [
            {
                "normalized_item_id": 1,
                "mentions": [
                    {
                        "action": "attach",
                        "event_key": "event-a",
                        "event_family": "gameplay_balance",
                        "relation": "reports",
                    }
                ],
            },
            {
                "normalized_item_id": 2,
                "mentions": [
                    {"action": "attach", "event_key": "event-b", "relation": "reports"}
                ],
            },
        ]
    }

    report = evaluate(fixture, predictions)

    assert report["counts"] == {
        "exact": 1,
        "mismatch": 1,
        "missed_membership": 1,
        "extra_membership": 1,
        "missing_prediction": 0,
    }


def test_saved_live_sample_keeps_predictions_separate_from_offline_labels() -> None:
    result = json.loads(LIVE_RESULT.read_text())

    assert len(result["case_ids"]) == 5
    assert result["score"]["item_count"] == 7
    assert result["score"]["counts"] == {"exact": 7, "missing_prediction": 0}
    assert len(result["raw_decisions"]) == 7
