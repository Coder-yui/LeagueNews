"""Reset ALL downstream-derived data in a LOCAL development database.

Preserves (never touches):
  - RawItem and RawItemSourcePayload (immutable source evidence)
  - Source / SourceCollectionSchedule / ConnectorRun
  - MediaAsset (original binary media, never rewrites)
  - KnowledgeRule / GlossaryTerm
  - OCRProfile / OCRTestRun

Removes (per AGENTS.md local downstream-reset rules):
  - Pipeline layer: PipelineJob, PipelineCorrection, ProcessingCheckpoint,
    ProcessingRun, ReviewTask
  - Publication layer: NormalizedItem (+ revisions + media links)
  - Media derivation: MediaExtraction
  - Event layer: Event + mentions + revisions + aggregation runs
  - Digest layer: DailyReport + items
  - Notifications: NotificationOutbox

This script intentionally mirrors `reset_event_layer.py` safety gates
(localhost + exact database name) and runs DRY-RUN by default.
"""

from __future__ import annotations

import argparse

from sqlalchemy import delete, func, select

import app.models  # noqa: F401
from app.core.database import SessionLocal, engine
from app.models.collection_schedule import SourceCollectionSchedule
from app.models.connector_run import ConnectorRun
from app.models.daily_report import DailyReport, DailyReportItem
from app.models.event import Event, EventAggregationRun, EventMention, EventRevision
from app.models.media_asset import MediaAsset
from app.models.media_extraction import MediaExtraction
from app.models.normalized_item import (
    NormalizedItem,
    NormalizedItemMediaExtraction,
    NormalizedItemRevision,
)
from app.models.notification import NotificationOutbox
from app.models.ocr_lab import OCRProfile, OCRTestRun
from app.models.pipeline import PipelineCorrection, PipelineJob, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.raw_item_source_payload import RawItemSourcePayload
from app.models.source import Source
from app.models.workflow import GlossaryTerm, KnowledgeRule, ProcessingRun, ReviewTask


PRESERVED_TABLE_LABELS = (
    ("sources", Source),
    ("raw_items", RawItem),
    ("raw_item_source_payloads", RawItemSourcePayload),
    ("media_assets", MediaAsset),
    ("source_collection_schedules", SourceCollectionSchedule),
    ("connector_runs", ConnectorRun),
    ("knowledge_rules", KnowledgeRule),
    ("glossary_terms", GlossaryTerm),
    ("ocr_profiles", OCRProfile),
    ("ocr_test_runs", OCRTestRun),
)

# Deletion order matters for foreign-key safety.  We delete from leaf tables
# (children that carry FKs) toward root tables (parents referenced by FKs).
# Key constraint: pipeline_corrections references normalized_items with
# ondelete=RESTRICT, so corrections MUST be deleted before normalized_items.
DERIVED_TABLE_DELETE_ORDER = (
    # Deepest leaves: pure child tables (CASCADE / RESTRICT FKs to parents)
    ("daily_report_items", DailyReportItem),
    ("event_mentions", EventMention),
    ("event_revisions", EventRevision),
    ("normalized_item_media_extractions", NormalizedItemMediaExtraction),
    ("normalized_item_revisions", NormalizedItemRevision),
    ("review_tasks", ReviewTask),
    # Parents of the leaves above (now free of child FKs)
    ("daily_reports", DailyReport),
    ("events", Event),
    ("event_aggregation_runs", EventAggregationRun),
    # Pipeline layer.  pipeline_jobs -> (corrections, runs, checkpoints) all
    # use SET NULL, so jobs go first.  checkpoints+corrections reference
    # normalized_items with SET NULL / RESTRICT, so both must be cleared
    # BEFORE normalized_items.
    ("pipeline_jobs", PipelineJob),
    ("processing_checkpoints", ProcessingCheckpoint),
    ("pipeline_corrections", PipelineCorrection),
    # processing_runs FK targets: raw_items(CASCADE), self-ref SET NULL,
    # corrections SET NULL.  All referrers (jobs/checkpoints/corrections/
    # reviews) are already gone or SET NULL.
    ("processing_runs", ProcessingRun),
    # Publication root: incoming RESTRICT FKs from corrections/checkpoints
    # + mentions/daily_report_items are all cleared by this point.
    ("normalized_items", NormalizedItem),
    # Media derivations.  normalized_item_media_extractions (RESTRICT FK to
    # media_extractions) is already deleted above.
    ("media_extractions", MediaExtraction),
    # Standalone outbox with no incoming FKs.
    ("notification_outbox", NotificationOutbox),
)


def _counts(db) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label, model in PRESERVED_TABLE_LABELS + DERIVED_TABLE_DELETE_ORDER:
        counts[label] = int(db.scalar(select(func.count()).select_from(model)) or 0)
    return counts


def _validate_local_database() -> None:
    if engine.url.host not in {"localhost", "127.0.0.1", "::1", None}:
        raise RuntimeError(f"refusing reset for non-local database host: {engine.url.host}")
    if engine.url.database != "lol_daily_intel":
        raise RuntimeError(f"refusing unexpected database: {engine.url.database}")


def reset_downstream(*, apply: bool) -> dict[str, dict[str, int]]:
    _validate_local_database()
    with SessionLocal() as db:
        before = _counts(db)
        print(
            {
                "database": engine.url.database,
                "apply": apply,
                "preserved_before": {k: before[k] for k, _ in PRESERVED_TABLE_LABELS},
                "derived_before": {k: before[k] for k, _ in DERIVED_TABLE_DELETE_ORDER},
            },
            flush=True,
        )
        if not apply:
            print("Dry run only. Add --apply to actually delete the rows listed above.")
            return {"before": before, "after": before}

        try:
            for _label, model in DERIVED_TABLE_DELETE_ORDER:
                db.execute(delete(model))
            # Flush/count/verify before committing. Any failed invariant rolls
            # back the destructive work while it is still reversible.
            db.flush()
            after = _counts(db)
            for label, _ in PRESERVED_TABLE_LABELS:
                if after[label] != before[label]:
                    raise RuntimeError(
                        f"Safety violation: preserved table {label!r} changed from "
                        f"{before[label]} to {after[label]}"
                    )
            for label, _ in DERIVED_TABLE_DELETE_ORDER:
                if after[label] != 0:
                    raise RuntimeError(
                        f"Derived table {label!r} was not fully cleared: {after[label]} rows remain"
                    )
            db.commit()
        except Exception:
            db.rollback()
            raise
        print(
            {
                "preserved_after": {k: after[k] for k, _ in PRESERVED_TABLE_LABELS},
                "derived_after": {k: after[k] for k, _ in DERIVED_TABLE_DELETE_ORDER},
            },
            flush=True,
        )
        return {"before": before, "after": after}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Reset ALL downstream-derived pipeline/publication/event/digest data "
            "on localhost only. Preserves RawItems, Sources, original media, "
            "source payloads, knowledge rules, glossary, connector runs, "
            "collection schedules, and OCR lab state. Dry-run by default."
        )
    )
    parser.add_argument("--apply", action="store_true", help="perform the reset")
    parser.add_argument("--yes", action="store_true", help="skip the exact interactive confirmation")
    args = parser.parse_args()
    if args.yes and not args.apply:
        parser.error("--yes requires --apply")
    if args.apply and not args.yes:
        expected = "yes, reset local downstream"
        if input(f"Type exactly to continue: {expected}\n> ").strip() != expected:
            print("Aborted. No changes made.")
            return
    reset_downstream(apply=args.apply)


if __name__ == "__main__":
    main()
