"""Hand-written answers exercise evaluators, not model quality."""

from app.orchestration.experiments.contracts import ExperimentDataset, ExperimentCase, CaseResult
from app.orchestration.experiments.evaluators import FrozenTaskEvaluator


def evaluate(target, labels, actual, status="succeeded", source="human_confirmed"):
    case = ExperimentCase(
        case_id="case",
        input={},
        labels={"values": labels, "source": source, "schema_version": "test"},
    )
    dataset = ExperimentDataset(
        name="manual-test", version="1", target=target, input_schema_version="1", cases=[case]
    )
    return FrozenTaskEvaluator(target).evaluate(
        dataset=dataset, cases=[CaseResult(case_id="case", actual=actual, status=status)]
    )


def test_multilabel_sets_partial_wrong_and_missing():
    labels = {"products": ["lol_pc", "tft"], "content_form": "original"}
    gold = evaluate(
        "message_analysis",
        labels,
        {"message_analysis": {"products": ["tft", "lol_pc"], "content_form": "original"}},
    )["gold_metrics"]
    assert gold["field_accuracy"] == {"content_form": 1, "products": 1}
    assert gold["multilabel"]["products"]["f1"] == 1
    partial = evaluate("message_analysis", labels, {"message_analysis": {"products": ["lol_pc"]}})[
        "gold_metrics"
    ]
    assert partial["multilabel"]["products"]["precision"] == 1
    assert partial["multilabel"]["products"]["recall"] == 0.5
    assert partial["multilabel"]["products"]["f1"] == 2 / 3
    for actual, status in [
        ({"message_analysis": {"products": ["unknown"]}}, "succeeded"),
        (None, "failed"),
    ]:
        wrong = evaluate("message_analysis", labels, actual, status)["gold_metrics"]
        assert wrong["field_accuracy"] == {"content_form": 0, "products": 0}
        assert wrong["fields"] == {"content_form": 1, "products": 1}


def test_unlabeled_and_missing_empty_plan_do_not_score_perfect():
    assert evaluate("message_analysis", {}, {}, source="unlabeled")["gold_metrics"] is None
    assert (
        evaluate("message_analysis", {"products": ["lol_pc"]}, {}, source="synthetic_fixture")[
            "gold_metrics"
        ]
        is None
    )
    assert (
        evaluate("featured_selection", {"selected_item_ids": []}, None, "failed")["gold_metrics"][
            "ranked_overlap"
        ]
        == 0
    )
    assert (
        evaluate("daily_report", {"note": "unlabeled"}, {})["gold_metrics"]["ranked_overlap"]
        is None
    )


def test_literal_summary_is_not_semantic_fact_accuracy():
    gold = evaluate(
        "message_analysis",
        {"summary_facts": ["上线", "免费"]},
        {"message_analysis": {"summary": "上线"}},
    )["gold_metrics"]
    assert gold["summary"]["literal_phrase_coverage"] == 0.5
    assert "fact_coverage" not in gold["summary"]
    assert gold["fields"] == {}


def scenario(ids=(91, 91), second_action="attach", missing=False):
    steps = []
    for i, event_id in enumerate(ids):
        steps.append(
            {
                "step_id": f"s{i}",
                "status": "failed" if missing and i else "succeeded",
                "actual": {
                    "event_decision": {
                        "mentions": [
                            {
                                "mention_index": 0,
                                "action": "create" if i == 0 else second_action,
                                "event_id": event_id,
                            }
                        ]
                    },
                    "event_memberships": [{"mention_index": 0, "event_id": event_id}],
                    "recalled_candidates": [{"event_id": ids[0]}] if i else [],
                },
            }
        )
    return {"steps": steps}


def test_scenario_relationships_do_not_depend_on_database_ids():
    labels = {
        "steps": {
            "s0": {"mentions": [{"mention_index": 0, "event_key": "story", "action": "create"}]},
            "s1": {"mentions": [{"mention_index": 0, "event_key": "story", "action": "attach"}]},
        }
    }
    for ids in [(91, 91), (812, 812)]:
        gold = evaluate("event_aggregation", labels, scenario(ids))["gold_metrics"]
        assert gold["membership_recall"] == gold["relationship_recall"] == 1
        assert gold["candidate_recall"] == 1
        assert gold["candidate_recall_denominator"] == 1
        assert gold["false_split_count"] == gold["false_merge_count"] == 0
    split = evaluate("event_aggregation", labels, scenario((1, 2), "create"))["gold_metrics"]
    assert split["false_split_count"] == 1
    assert split["decision_after_recall_errors"] == 1
    missing = evaluate("event_aggregation", labels, scenario(missing=True))["gold_metrics"]
    assert missing["membership_recall"] == 0.5
    assert missing["missing_steps"] == 1


def test_multi_event_mentions_and_false_merge():
    labels = {
        "mentions": [{"mention_index": 0, "event_key": "A"}, {"mention_index": 1, "event_key": "B"}]
    }
    actual = {
        "event_decision": {
            "mentions": [
                {"mention_index": 0, "action": "attach", "event_id": 70},
                {"mention_index": 1, "action": "attach", "event_id": 80},
            ]
        }
    }
    assert evaluate("event_aggregation", labels, actual)["gold_metrics"]["false_merge_count"] == 0
    actual["event_decision"]["mentions"][1]["event_id"] = 70
    assert evaluate("event_aggregation", labels, actual)["gold_metrics"]["false_merge_count"] == 1


def test_score_missing_coverage_is_explicit():
    gold = evaluate("importance_scoring", {"importance_score": 0.8}, None, "failed")["gold_metrics"]
    assert gold["score_mae"] is None
    assert gold["score_labeled_cases"] == gold["score_missing_outputs"] == 1


def test_missing_empty_plan_fields_are_not_equal_to_labeled_empty_selections():
    assert evaluate("featured_selection", {"selected_item_ids": []}, {"featured_plan": {}})["gold_metrics"]["ranked_overlap"] == 0
    assert evaluate("daily_report", {"sections": {}}, {"daily_plan": {}})["gold_metrics"]["ranked_overlap"] == 0
