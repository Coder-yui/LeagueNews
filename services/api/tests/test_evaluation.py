import json
from pathlib import Path

from app.evaluation.runner import compare, load_jsonl


def test_evaluation_compares_current_message_stages() -> None:
    dataset = [
        {
            "dataset_version": "message-v1",
            "case_id": "a",
            "task": "relevance",
            "expected": {"decision": "relevant"},
        },
        {
            "dataset_version": "message-v1",
            "case_id": "b",
            "task": "importance",
            "expected": {"score": 0.8},
        },
    ]
    result = compare(
        dataset,
        [
            {"case_id": "a", "actual": {"decision": "relevant"}, "candidate": "p1"},
            {"case_id": "b", "actual": {"score": 0.6}, "candidate": "p1"},
        ],
    )

    assert result["exact_match"] == 0.5
    assert result["by_task"]["relevance"]["exact_match"] == 1
    assert result["errors"][0]["case_id"] == "b"


def test_message_analysis_dataset_validates_controlled_taxonomy(tmp_path: Path) -> None:
    path = tmp_path / "message.jsonl"
    path.write_text(
        json.dumps(
            {
                "dataset_version": "message-v1",
                "case_id": "classification-1",
                "task": "message_analysis",
                "input": {"raw_item_id": 1, "is_official_source": True},
                "expected": {
                    "products": ["lol_pc"],
                    "content_form": "original",
                },
            }
        ),
        encoding="utf-8",
    )

    assert load_jsonl(path)[0]["case_id"] == "classification-1"
