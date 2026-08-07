from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import SessionLocal
from app.evaluation.runner import load_jsonl
from app.models.event import (
    Event,
    EventAggregationRun,
    EventMessage,
    EventReviewTask,
)
from app.models.raw_item import RawItem
from app.models.workflow import ProcessingRun, ReviewTask
from app.services.event_candidates import aggregation_routes


def _latest_processing_run(db: Session, raw_item_id: int) -> ProcessingRun | None:
    return db.scalar(
        select(ProcessingRun)
        .where(ProcessingRun.raw_item_id == raw_item_id)
        .order_by(ProcessingRun.id.desc())
        .limit(1)
    )


def _review_audit(db: Session, run: ProcessingRun | None) -> list[dict[str, object]]:
    if run is None:
        return []
    return [
        {
            "stage": review.stage,
            "status": review.status,
            "decision_source": review.decision_source,
            "policy_version": review.policy_version,
        }
        for review in db.scalars(
            select(ReviewTask)
            .where(ReviewTask.processing_run_id == run.id)
            .order_by(ReviewTask.id)
        )
    ]


def _event_audit(db: Session, normalized_item_id: int) -> list[dict[str, object]]:
    rows = db.execute(
        select(EventMessage, Event)
        .join(Event, Event.id == EventMessage.event_id)
        .where(
            EventMessage.normalized_item_id == normalized_item_id,
            EventMessage.membership_status == "active",
        )
        .order_by(Event.id)
    )
    return [
        {
            "event_id": event.id,
            "aggregation_key": event.aggregation_key,
            "event_kind": event.event_kind,
            "aggregation_strategy": event.aggregation_strategy,
            "product_scope": event.product_scope,
            "lifecycle_status": event.lifecycle_status,
            "importance_score": event.importance_score,
            "importance_policy_version": event.importance_policy_version,
            "independent_source_count": event.independent_source_count,
            "membership_role": membership.membership_role,
            "evidence_stance": membership.evidence_stance,
            "update_kind": membership.update_kind,
            "timeline_note": membership.timeline_note,
        }
        for membership, event in rows
    ]


def _event_run_audit(
    db: Session, normalized_item_id: int
) -> dict[str, object] | None:
    run = db.scalar(
        select(EventAggregationRun)
        .where(EventAggregationRun.normalized_item_id == normalized_item_id)
        .order_by(EventAggregationRun.id.desc())
        .limit(1)
    )
    if run is None:
        return None
    reviews = list(
        db.scalars(
            select(EventReviewTask)
            .where(EventReviewTask.event_aggregation_run_id == run.id)
            .order_by(EventReviewTask.id)
        )
    )
    return {
        "run_id": run.id,
        "status": run.status,
        "outcome": run.outcome,
        "error_message": run.error_message,
        "reviews": [
            {
                "status": review.status,
                "decision_source": review.decision_source,
                "policy_version": review.policy_version,
            }
            for review in reviews
        ],
    }


def _prediction(db: Session, case: dict[str, Any]) -> dict[str, Any]:
    raw_item_id = int(case["input"]["raw_item_id"])
    raw_item = db.get(RawItem, raw_item_id)
    if raw_item is None:
        raise RuntimeError(f"RawItem {raw_item_id} does not exist")
    run = _latest_processing_run(db, raw_item_id)
    context = run.context if run is not None else {}
    gate = context.get("evidence_gate", {})
    relevance = context.get("relevance_decision", {})
    actual: dict[str, object] = {
        "evidence_decision": gate.get("decision"),
        "requires_manual_review": gate.get("requires_manual_review"),
    }
    if isinstance(relevance, dict) and relevance:
        actual.update(
            {
                "is_lol_relevant": relevance.get("is_lol_relevant"),
                "product_scope": relevance.get("product_scope"),
            }
        )

    item = raw_item.normalized_item
    event_memberships: list[dict[str, object]] = []
    event_run = None
    if item is not None:
        routes = aggregation_routes(item)
        route_keys = [
            route.aggregation_key
            for route in routes
            if route.aggregation_key is not None
        ]
        event_memberships = _event_audit(db, item.id)
        event_run = _event_run_audit(db, item.id)
        actual.update(
            {
                "is_lol_relevant": True,
                "product_scope": item.product_scope,
                "source_kind": item.source_kind,
                "information_stage": item.information_stage,
                "content_form": item.content_form,
                "primary_topic": item.primary_topic,
                "subtopic": item.subtopic,
                "importance_score": item.importance_score,
                "priority_score": item.priority_score,
                "route_keys": route_keys,
                "event_ids": [
                    int(membership["event_id"])
                    for membership in event_memberships
                ],
            }
        )
        if routes:
            actual.update(
                {
                    "event_kind": routes[0].event_kind,
                    "aggregation_strategy": routes[0].aggregation_strategy,
                }
            )
    else:
        actual.update({"route_keys": [], "event_ids": []})

    output = dict(case)
    output["candidate"] = {
        "model": item.analysis_model if item is not None else None,
        "ontology": item.ontology_version if item is not None else None,
        "importance_policy": (
            item.importance_policy_version if item is not None else None
        ),
    }
    output["actual"] = actual
    output["audit"] = {
        "processing_run": (
            {
                "run_id": run.id,
                "status": run.status,
                "outcome": run.outcome,
                "current_stage": run.current_stage,
                "error_message": run.error_message,
            }
            if run is not None
            else None
        ),
        "message_reviews": _review_audit(db, run),
        "event_run": event_run,
        "event_memberships": event_memberships,
    }
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export current pipeline and event results for a RawItem dataset."
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset = load_jsonl(args.dataset)
    with SessionLocal() as db:
        predictions = [_prediction(db, case) for case in dataset]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in predictions
        ),
        encoding="utf-8",
    )
    print({"dataset": str(args.dataset), "predictions": len(predictions)})


if __name__ == "__main__":
    main()
