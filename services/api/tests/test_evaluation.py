from app.evaluation.runner import compare


def test_evaluation_compares_candidates_and_reports_errors() -> None:
    dataset = [
        {
            "dataset_version": "v1",
            "case_id": "a",
            "task": "relevance",
            "expected": {"relevant": True},
        },
        {
            "dataset_version": "v1",
            "case_id": "b",
            "task": "classification",
            "expected": "patch",
        },
    ]
    result = compare(
        dataset,
        [
            {"case_id": "a", "actual": {"relevant": True}, "candidate": "p1"},
            {"case_id": "b", "actual": "esports", "candidate": "p1"},
        ],
    )
    assert result["exact_match"] == 0.5
    assert result["by_task"]["relevance"]["exact_match"] == 1
    assert result["errors"][0]["case_id"] == "b"


def test_evaluation_reports_event_recall_and_merge_errors() -> None:
    dataset = [
        {
            "dataset_version": "events-v1",
            "case_id": "recall-1",
            "task": "event_candidate",
            "expected": {"relevant_event_id": 9},
        },
        {
            "dataset_version": "events-v1",
            "case_id": "merge-1",
            "task": "event_decision",
            "expected": {"decision": "create"},
        },
    ]
    predictions = [
        {
            "case_id": "recall-1",
            "actual": {"candidate_event_ids": [2, 9]},
        },
        {
            "case_id": "merge-1",
            "actual": {"decision": "update"},
        },
    ]
    result = compare(dataset, predictions)
    assert result["event_retrieval"]["recall_at_5"] == 1
    assert result["event_retrieval"]["false_merge_rate"] == 1
