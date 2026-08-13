from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.database import SessionLocal
from app.domain.event_admission import derive_event_space
from app.models.normalized_item import NormalizedItem
from app.services.llm import LLMClient
from app.workflows.event_aggregation import _message_payload
from scripts.evaluate_event_aggregation import DEFAULT_FIXTURE, evaluate


DEFAULT_CASE_IDS = (
    "weekly-free-champions-ignore",
    "nasus-pbe-balance-corroboration",
    "alistar-icon-original-and-repost",
    "blg-jdg-specific-fixture",
    "tft-character-and-activity-announcement",
)


@dataclass
class VirtualEvent:
    event_id: int
    event_key: str
    event_family: str
    products: list[str]
    title: str
    summary: str
    canonical_anchors: dict[str, Any] = field(default_factory=dict)
    latest_development: str = ""
    key_facts: list[dict[str, Any]] = field(default_factory=list)

    def candidate(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_family": self.event_family,
            "products": self.products,
            "canonical_anchors": self.canonical_anchors,
            "title": self.title,
            "current_summary": self.summary,
            "latest_development": self.latest_development,
            "key_facts": self.key_facts,
            "lifecycle_status": "developing",
            "last_seen_at": None,
            "recall_score": 1.0,
            "recall_reasons": ["evaluation_case_context"],
        }


async def run_case(
    case: dict[str, Any], *, client: LLMClient, db
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    virtual_events: dict[int, VirtualEvent] = {}
    next_event_id = 1
    predictions: list[dict[str, Any]] = []
    raw_decisions: list[dict[str, Any]] = []

    for item_spec in case["items"]:
        item_id = int(item_spec["normalized_item_id"])
        item = db.get(NormalizedItem, item_id)
        if item is None:
            raise RuntimeError(f"NormalizedItem {item_id} not found")
        message, _truncation = _message_payload(item)
        event_space = derive_event_space(item)
        result = await client.aggregate_events(
            message=message,
            possible_event_families=list(event_space.possible_families),
            candidates=[event.candidate() for event in virtual_events.values()],
        )
        expected_creates = [
            value
            for value in item_spec.get("expected", [])
            if value.get("action") == "create"
        ]
        unmatched_expected_creates = list(expected_creates)
        predicted_mentions: list[dict[str, Any]] = []
        for decision in result.mentions:
            if decision.action == "ignore":
                continue
            if decision.action == "create":
                seed = decision.new_event
                if seed is None or decision.event_family is None:
                    continue
                expected_index = next(
                    (
                        index
                        for index, expected in enumerate(unmatched_expected_creates)
                        if not expected.get("event_family")
                        or expected.get("event_family") == decision.event_family
                    ),
                    None,
                )
                if expected_index is None:
                    event_key = (
                        f"unexpected:{case['case_id']}:{item_id}:{decision.mention_index}"
                    )
                else:
                    event_key = str(
                        unmatched_expected_creates.pop(expected_index)["event_key"]
                    )
                virtual = VirtualEvent(
                    event_id=next_event_id,
                    event_key=event_key,
                    event_family=decision.event_family,
                    products=list(item.products),
                    title=seed.title,
                    summary=seed.summary,
                    canonical_anchors=seed.canonical_anchors,
                    latest_development=seed.latest_development,
                    key_facts=seed.key_facts,
                )
                virtual_events[next_event_id] = virtual
                next_event_id += 1
            else:
                virtual = virtual_events.get(int(decision.event_id or 0))
                event_key = (
                    virtual.event_key
                    if virtual is not None
                    else f"unknown-candidate:{decision.event_id}"
                )
                if virtual is not None and decision.projection is not None:
                    virtual.title = decision.projection.title or virtual.title
                    virtual.summary = decision.projection.summary or virtual.summary
                    virtual.latest_development = (
                        decision.projection.latest_development
                        or virtual.latest_development
                    )
                    virtual.key_facts = decision.projection.key_facts or virtual.key_facts
            predicted_mentions.append(
                {
                    "action": decision.action,
                    "event_key": event_key,
                    "relation": decision.relation,
                    "event_family": decision.event_family,
                }
            )
        predictions.append(
            {"normalized_item_id": item_id, "mentions": predicted_mentions}
        )
        raw_decisions.append(
            {
                "normalized_item_id": item_id,
                "decision": result.model_dump(mode="json"),
            }
        )
    return predictions, raw_decisions


async def run(case_ids: tuple[str, ...], fixture_path: Path) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text())
    selected = [case for case in fixture["cases"] if case["case_id"] in case_ids]
    missing = set(case_ids) - {case["case_id"] for case in selected}
    if missing:
        raise ValueError(f"unknown case ids: {sorted(missing)}")
    if any(case.get("preexisting_event_keys") for case in selected):
        raise ValueError("live runner currently requires cases without preexisting Event seeds")
    client = LLMClient()
    if not client.enabled:
        raise RuntimeError("LLM is not configured")
    all_predictions: list[dict[str, Any]] = []
    raw_decisions: list[dict[str, Any]] = []
    with SessionLocal() as db:
        for case in selected:
            predictions, decisions = await run_case(case, client=client, db=db)
            all_predictions.extend(predictions)
            raw_decisions.extend(decisions)
    selected_fixture = {**fixture, "cases": selected}
    prediction_payload = {"items": all_predictions}
    return {
        "fixture_version": fixture["version"],
        "evaluated_at": datetime.now(UTC).isoformat(),
        "case_ids": [case["case_id"] for case in selected],
        "predictions": prediction_payload,
        "score": evaluate(selected_fixture, prediction_payload),
        "raw_decisions": raw_decisions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a non-persisting live LLM sample against real NormalizedItems."
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    case_ids = tuple(args.case_ids) if args.case_ids else DEFAULT_CASE_IDS
    report = asyncio.run(run(case_ids, args.fixture))
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n")
    print(encoded)


if __name__ == "__main__":
    main()
