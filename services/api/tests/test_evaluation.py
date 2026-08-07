import json
from pathlib import Path

import pytest

from app.domain.evidence import evaluate_evidence_gate
from app.evaluation.runner import compare, load_jsonl
from app.models.media_asset import MediaAsset
from app.models.raw_item import RawItem


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


def test_ontology_evaluation_uses_bands_prefixes_and_controlled_labels() -> None:
    dataset = [
        {
            "dataset_version": "raw-items-ontology-v2",
            "case_id": "patch-1",
            "task": "ontology_v2",
            "input": {"raw_item_id": 1, "is_revision_head": True},
            "expected": {
                "primary_topic": "patch",
                "subtopic": "patch_notes",
                "importance_band": [0.88, 0.95],
                "route_key_prefixes": ["patch:lol_pc:26.16"],
            },
            "coverage_tags": ["patch"],
        }
    ]

    result = compare(
        dataset,
        [
            {
                "case_id": "patch-1",
                "actual": {
                    "primary_topic": "patch",
                    "subtopic": "patch_notes",
                    "importance_score": 0.92,
                    "route_keys": ["patch:lol_pc:26.16"],
                },
            }
        ],
    )

    assert result["exact_match"] == 1
    assert result["ontology_invariants"] == {}
    assert result["coverage"] == {"patch": 1}


def test_ontology_evaluation_reports_unknown_route_and_invalid_label() -> None:
    dataset = [
        {
            "dataset_version": "raw-items-ontology-v2",
            "case_id": "bad-1",
            "task": "ontology_v2",
            "input": {"raw_item_id": 1, "is_revision_head": True},
            "expected": {"primary_topic": "patch"},
            "coverage_tags": ["patch"],
        }
    ]

    result = compare(
        dataset,
        [
            {
                "case_id": "bad-1",
                "actual": {
                    "primary_topic": "made_up",
                    "route_keys": ["patch:unknown:26.16"],
                },
            }
        ],
    )

    assert result["ontology_invariants"] == {
        "invalid_primary_topic": 1,
        "primary_topic": 1,
        "unknown_route_key": 1,
    }


def test_ontology_loader_rejects_historical_revision(tmp_path: Path) -> None:
    path = tmp_path / "historical.jsonl"
    path.write_text(
        json.dumps(
            {
                "dataset_version": "raw-items-ontology-v2",
                "case_id": "old-1",
                "task": "ontology_v2",
                "input": {"raw_item_id": 1, "is_revision_head": False},
                "expected": {"primary_topic": "patch"},
                "coverage_tags": ["revision"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="only revision heads"):
        load_jsonl(path)


def test_real_raw_item_regression_set_is_broad_and_self_contained() -> None:
    path = Path(__file__).parents[1] / "evaluation" / "raw-items-ontology-v2.jsonl"

    dataset = load_jsonl(path)

    assert len(dataset) >= 45
    raw_ids = [row["input"]["raw_item_id"] for row in dataset]
    assert len(raw_ids) == len(set(raw_ids))
    assert all(row["input"]["is_revision_head"] is True for row in dataset)
    assert all(row["input"].get("title") is not None for row in dataset)
    assert all(row["input"].get("text") is not None for row in dataset)
    tags = {tag for row in dataset for tag in row["coverage_tags"]}
    assert {
        "patch",
        "designer_ocr",
        "pbe",
        "tft",
        "commerce",
        "activity",
        "game_mode",
        "skin",
        "esports",
        "roster",
        "disciplinary",
        "service",
        "physical_merch",
        "community",
        "irrelevant",
        "wild_rift",
        "2xko",
        "image_only",
        "link_only",
        "repost",
    } <= tags


def test_event_chain_regression_set_is_representative_and_complete() -> None:
    path = (
        Path(__file__).parents[1]
        / "evaluation"
        / "raw-items-event-chains-v1.jsonl"
    )

    dataset = load_jsonl(path)
    groups = {
        row["expected"]["event_group"]
        for row in dataset
    }
    stages = {
        row["input"]["chain_stage"]
        for row in dataset
    }

    assert len(dataset) >= 20
    assert {
        "patch-26.15",
        "transfer-2026-flandre",
        "classic-pass-act1",
        "lpl-2026-07-30-matchday",
        "league-classic-release",
        "negative-t1-skins",
        "negative-shop-rotation",
    } <= groups
    assert {
        "designer_preview",
        "official_release",
        "destination_rumor",
        "official_launch_details",
        "official_schedule",
        "official_series_result",
        "postmatch_discussion",
    } <= stages
    assert all(row["input"]["is_revision_head"] is True for row in dataset)


def test_real_raw_item_evidence_expectations_match_gate() -> None:
    path = Path(__file__).parents[1] / "evaluation" / "raw-items-ontology-v2.jsonl"

    for case in load_jsonl(path):
        input_data = case["input"]
        blocks = (
            [{"id": "b0001", "type": "paragraph", "text": input_data["text"]}]
            if input_data["text"]
            else []
        )
        raw_item = RawItem(
            source_id=1,
            native_title=input_data["title"] or None,
            content_blocks=blocks,
        )
        raw_item.media_assets = [
            MediaAsset(block_index=index, mime_type="image/png")
            for index in range(input_data["image_count"])
        ]

        gate = evaluate_evidence_gate(
            raw_item,
            designer_patch_images=input_data["designer_patch_images"],
        )

        assert gate.decision == case["expected"]["evidence_decision"], case[
            "case_id"
        ]
        assert gate.requires_manual_review is case["expected"][
            "requires_manual_review"
        ]
