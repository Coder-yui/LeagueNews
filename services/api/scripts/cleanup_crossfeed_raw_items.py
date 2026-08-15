"""Remove STRAY cross-feed RawItems (X/Twitter "串台" posts) from a LOCAL dev DB.

A cross-feed raw_item is a non-retweet X record whose canonical URL envelope
author differs from the configured source. Candidate selection also requires a
matching raw_item_source_payload with no ``retweeted_tweet_id``; missing source
evidence is deliberately retained.

These tweets sneaked in through twscrape's recommendation/mention injection
in the user timeline endpoint before the connector-layer attribution filter
was added. We drop them together with any downstream rows they generated.

SAFETY GATES (mirrors reset_local_downstream.py):
  * Host must be localhost / 127.0.0.1 / ::1 / unix socket.
  * Database name must be exactly "lol_daily_intel" (the dev DB).
  * Default mode is DRY-RUN: pass --apply to actually delete.
  * Always prints the full candidate list before the prompt.
  * Always prints before/after row counts + post-delete assertions.
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import delete, func, select, text

import app.models  # noqa: F401
from app.core.database import SessionLocal, engine
from app.models.daily_report import DailyReportItem
from app.models.event import Event, EventAggregationRun, EventMention, EventRevision
from app.models.normalized_item import (
    NormalizedItem,
    NormalizedItemMediaExtraction,
    NormalizedItemRevision,
)
from app.models.pipeline import PipelineCorrection, PipelineJob, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.raw_item_source_payload import RawItemSourcePayload
from app.models.workflow import ProcessingRun, ReviewTask
from app.services.event_metrics import refresh_event_metrics
from app.repositories.events import current_event_mention_conditions


# ---------------------------------------------------------------------------
# Safety gates (exact same rules as reset_local_downstream.py)
# ---------------------------------------------------------------------------


def _validate_local_database() -> None:
    print(f"[safety] DB URL     : {engine.url}")
    print(f"[safety] DB host    : {engine.url.host!r}")
    print(f"[safety] DB database: {engine.url.database!r}")
    if engine.url.host not in {"localhost", "127.0.0.1", "::1", None}:
        print(
            "[safety] ❌ REFUSING: non-local database host.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if engine.url.database != "lol_daily_intel":
        print(
            f"[safety] ❌ REFUSING: unexpected database name {engine.url.database!r}.",
            "This script is locked to the local dev DB `lol_daily_intel`.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    print("[safety] ✅ local dev DB confirmed.")


# ---------------------------------------------------------------------------
# Candidate discovery
# ---------------------------------------------------------------------------


CANDIDATE_SQL = text(
    """
    SELECT
        ri.id               AS raw_item_id,
        ri.external_id,
        ri.canonical_url,
        s.id                AS source_id,
        s.name              AS source_name,
        lower(s.external_key)   AS source_username,
        -- X post canonical URLs have the form:
        --   https://x.com/<username>/status/<tweet_id>
        -- split_part 1-indexed segments: 1=https: 2=(empty) 3=x.com 4=<username>
        lower(split_part(ri.canonical_url, '/', 4))    AS tweet_author_username,
        rsp.payload->>'retweeted_tweet_id'              AS rt_id,
        ri.published_at,
        ri.native_title,
        ri.content_blocks::text AS cb_json
    FROM raw_items ri
    JOIN sources s ON s.id = ri.source_id
    JOIN raw_item_source_payloads rsp ON rsp.raw_item_id = ri.id
    WHERE s.connector_type = 'x_twitter'
      AND ri.canonical_url ~ '^https?://(x|twitter)\.com/'
      AND lower(split_part(ri.canonical_url, '/', 4)) <> ''
      AND lower(split_part(ri.canonical_url, '/', 4)) <> lower(s.external_key)
      -- Must have a source payload so we can reliably check retweet status.
      -- When payload is missing we refuse to delete (could be a retweet we
      -- can't see the marker for — conservative keep).
      AND rsp.raw_item_id IS NOT NULL
      AND rsp.payload->>'retweeted_tweet_id' IS NULL
    ORDER BY ri.id
    """
)


def find_candidates(db) -> list[dict]:
    return [dict(r) for r in db.execute(CANDIDATE_SQL).mappings()]


def _content_snippet(cb_json: str | None, width: int = 140) -> str:
    if not cb_json:
        return ""
    try:
        blocks = json.loads(cb_json)
    except Exception:
        return "(content unreadable)"
    parts: list[str] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t in {"paragraph", "heading"} and b.get("text"):
            parts.append(b["text"].strip())
        if sum(len(p) for p in parts) >= width:
            break
    joined = " / ".join(parts)
    if len(joined) > width:
        joined = joined[: width - 1] + "…"
    return joined


def print_candidates(rows: list[dict]) -> None:
    print()
    print(f"=== Candidate cross-feed RawItems ({len(rows)}) ===")
    if not rows:
        print("  (none matched the filter — nothing to clean up)")
        return
    for r in rows:
        snippet = _content_snippet(r["cb_json"])
        print(
            f"  RawItem#{r['raw_item_id']:<5} "
            f"Source#{r['source_id']:>2} {r['source_name']}"
        )
        print(
            f"      tweet author=@{r['tweet_author_username']:<20} "
            f"(expected source @{r['source_username']})  "
            f"published={r['published_at']}"
        )
        print(f"      url   : {r['canonical_url']}")
        if snippet:
            print(f"      body  : {snippet}")
        print()


# ---------------------------------------------------------------------------
# Downstream reference counts (read-only reporting)
# ---------------------------------------------------------------------------


def _scalar(db, sql: str, **params) -> int:
    return int(db.execute(text(sql), params).scalar_one() or 0)


def print_downstream_refs(db, raw_item_ids: tuple[int, ...]) -> bool:
    print(f"=== Downstream references for {len(raw_item_ids)} candidate raw_item_id(s) ===")
    any_ref = False

    def row(table: str, note: str, sql: str, **params) -> None:
        nonlocal any_ref
        cnt = _scalar(db, sql, **params)
        flag = " ⚠️ REFERENCED" if cnt else " — OK"
        if cnt:
            any_ref = True
        print(f"  {table:<35} {note:<28} → {cnt:>5} rows {flag}")

    # Child tables (references normalized_items.id via candidate raw_item_id)
    row(
        "daily_report_items",
        "via normalized_items",
        """
        SELECT COUNT(*) FROM daily_report_items dri
        JOIN normalized_items ni ON ni.id = dri.normalized_item_id
        WHERE ni.raw_item_id = ANY(:ids)
        """,
        ids=raw_item_ids,
    )
    row(
        "event_mentions",
        "via normalized_items",
        """
        SELECT COUNT(*) FROM event_mentions em
        JOIN normalized_items ni ON ni.id = em.normalized_item_id
        WHERE ni.raw_item_id = ANY(:ids)
        """,
        ids=raw_item_ids,
    )
    row(
        "event_aggregation_runs",
        "via normalized_items",
        """
        SELECT COUNT(*) FROM event_aggregation_runs ear
        JOIN normalized_items ni ON ni.id = ear.normalized_item_id
        WHERE ni.raw_item_id = ANY(:ids)
        """,
        ids=raw_item_ids,
    )
    row(
        "normalized_item_media_extractions",
        "via normalized_items",
        """
        SELECT COUNT(*) FROM normalized_item_media_extractions nme
        JOIN normalized_items ni ON ni.id = nme.normalized_item_id
        WHERE ni.raw_item_id = ANY(:ids)
        """,
        ids=raw_item_ids,
    )
    # Tables referencing raw_item_id directly.
    row(
        "review_tasks",
        "via processing_runs",
        """
        SELECT COUNT(*) FROM review_tasks rt
        JOIN processing_runs pr ON pr.id = rt.processing_run_id
        WHERE pr.raw_item_id = ANY(:ids)
        """,
        ids=raw_item_ids,
    )
    row(
        "pipeline_jobs",
        "raw_item_id",
        "SELECT COUNT(*) FROM pipeline_jobs WHERE raw_item_id = ANY(:ids)",
        ids=raw_item_ids,
    )
    row(
        "processing_checkpoints",
        "raw_item_id",
        "SELECT COUNT(*) FROM processing_checkpoints WHERE raw_item_id = ANY(:ids)",
        ids=raw_item_ids,
    )
    row(
        "pipeline_corrections",
        "raw_item_id",
        "SELECT COUNT(*) FROM pipeline_corrections WHERE raw_item_id = ANY(:ids)",
        ids=raw_item_ids,
    )
    row(
        "processing_runs",
        "raw_item_id",
        "SELECT COUNT(*) FROM processing_runs WHERE raw_item_id = ANY(:ids)",
        ids=raw_item_ids,
    )
    row(
        "normalized_item_revisions",
        "via normalized_items",
        """
        SELECT COUNT(*) FROM normalized_item_revisions nir
        JOIN normalized_items ni ON ni.id = nir.normalized_item_id
        WHERE ni.raw_item_id = ANY(:ids)
        """,
        ids=raw_item_ids,
    )
    row(
        "normalized_items",
        "raw_item_id",
        "SELECT COUNT(*) FROM normalized_items WHERE raw_item_id = ANY(:ids)",
        ids=raw_item_ids,
    )
    row(
        "raw_item_source_payloads",
        "raw_item_id",
        "SELECT COUNT(*) FROM raw_item_source_payloads WHERE raw_item_id = ANY(:ids)",
        ids=raw_item_ids,
    )
    return any_ref


# ---------------------------------------------------------------------------
# Deletion plan (child → parent, each step scoped to candidate raw_item_ids)
# ---------------------------------------------------------------------------


def _delete_ct(
    db,
    *,
    delete_sql: str,
    dry_run_sql: str,
    dry_run: bool,
    **params,
) -> int:
    """Return how many rows would be / were deleted by the statement.

    dry_run_sql is a read-only COUNT(*) query that mirrors the DELETE's
    where-clause scoping. Supplied explicitly rather than auto-rewriting the
    DELETE because DELETE ... USING has no direct SELECT COUNT(*) equivalent.
    """
    if dry_run:
        return _scalar(db, dry_run_sql, **params)
    res = db.execute(text(delete_sql), params)
    return len(list(res.all()))


def perform_cleanup(db, raw_item_ids: tuple[int, ...], *, dry_run: bool) -> dict[str, int]:
    counts: dict[str, int] = {}
    ids = list(raw_item_ids)

    # 1. daily_report_items —→ FK: normalized_items.id (RESTRICT)
    counts["daily_report_items"] = _delete_ct(
        db,
        delete_sql="""
            DELETE FROM daily_report_items dri
            USING normalized_items ni
            WHERE ni.id = dri.normalized_item_id AND ni.raw_item_id = ANY(:ids)
            RETURNING dri.id
        """,
        dry_run_sql="""
            SELECT COUNT(*) FROM daily_report_items dri
            JOIN normalized_items ni ON ni.id = dri.normalized_item_id
            WHERE ni.raw_item_id = ANY(:ids)
        """,
        dry_run=dry_run,
        ids=ids,
    )

    # 2. event_mentions —→ FK: normalized_items.id (RESTRICT)
    counts["event_mentions"] = _delete_ct(
        db,
        delete_sql="""
            DELETE FROM event_mentions em
            USING normalized_items ni
            WHERE ni.id = em.normalized_item_id AND ni.raw_item_id = ANY(:ids)
            RETURNING em.id
        """,
        dry_run_sql="""
            SELECT COUNT(*) FROM event_mentions em
            JOIN normalized_items ni ON ni.id = em.normalized_item_id
            WHERE ni.raw_item_id = ANY(:ids)
        """,
        dry_run=dry_run,
        ids=ids,
    )

    # 2b. event_aggregation_runs —→ FK: normalized_items.id (RESTRICT)
    counts["event_aggregation_runs"] = _delete_ct(
        db,
        delete_sql="""
            DELETE FROM event_aggregation_runs ear
            USING normalized_items ni
            WHERE ni.id = ear.normalized_item_id AND ni.raw_item_id = ANY(:ids)
            RETURNING ear.id
        """,
        dry_run_sql="""
            SELECT COUNT(*) FROM event_aggregation_runs ear
            JOIN normalized_items ni ON ni.id = ear.normalized_item_id
            WHERE ni.raw_item_id = ANY(:ids)
        """,
        dry_run=dry_run,
        ids=ids,
    )

    # 3. normalized_item_media_extractions —→ FK: normalized_items.id (CASCADE, but manual scoping)
    counts["normalized_item_media_extractions"] = _delete_ct(
        db,
        delete_sql="""
            DELETE FROM normalized_item_media_extractions nme
            USING normalized_items ni
            WHERE ni.id = nme.normalized_item_id AND ni.raw_item_id = ANY(:ids)
            RETURNING nme.normalized_item_id
        """,
        dry_run_sql="""
            SELECT COUNT(*) FROM normalized_item_media_extractions nme
            JOIN normalized_items ni ON ni.id = nme.normalized_item_id
            WHERE ni.raw_item_id = ANY(:ids)
        """,
        dry_run=dry_run,
        ids=ids,
    )

    # 4. review_tasks —→ FK: processing_runs.id (CASCADE in schema, but manual scoping)
    counts["review_tasks"] = _delete_ct(
        db,
        delete_sql="""
            DELETE FROM review_tasks rt
            USING processing_runs pr
            WHERE pr.id = rt.processing_run_id AND pr.raw_item_id = ANY(:ids)
            RETURNING rt.id
        """,
        dry_run_sql="""
            SELECT COUNT(*) FROM review_tasks rt
            JOIN processing_runs pr ON pr.id = rt.processing_run_id
            WHERE pr.raw_item_id = ANY(:ids)
        """,
        dry_run=dry_run,
        ids=ids,
    )

    # 5. pipeline_jobs —→ FK: raw_item_id (RESTRICT)
    counts["pipeline_jobs"] = _delete_ct(
        db,
        delete_sql="DELETE FROM pipeline_jobs WHERE raw_item_id = ANY(:ids) RETURNING id",
        dry_run_sql="SELECT COUNT(*) FROM pipeline_jobs WHERE raw_item_id = ANY(:ids)",
        dry_run=dry_run,
        ids=ids,
    )

    # 6. processing_checkpoints —→ FK: raw_item_id (RESTRICT)
    counts["processing_checkpoints"] = _delete_ct(
        db,
        delete_sql="DELETE FROM processing_checkpoints WHERE raw_item_id = ANY(:ids) RETURNING id",
        dry_run_sql="SELECT COUNT(*) FROM processing_checkpoints WHERE raw_item_id = ANY(:ids)",
        dry_run=dry_run,
        ids=ids,
    )

    # 7. pipeline_corrections —→ FK: raw_item_id (RESTRICT) + normalized_item_id (RESTRICT)
    counts["pipeline_corrections"] = _delete_ct(
        db,
        delete_sql="DELETE FROM pipeline_corrections WHERE raw_item_id = ANY(:ids) RETURNING id",
        dry_run_sql="SELECT COUNT(*) FROM pipeline_corrections WHERE raw_item_id = ANY(:ids)",
        dry_run=dry_run,
        ids=ids,
    )

    # 8. processing_runs —→ FK: raw_item_id (CASCADE in schema, but manual scoping)
    counts["processing_runs"] = _delete_ct(
        db,
        delete_sql="DELETE FROM processing_runs WHERE raw_item_id = ANY(:ids) RETURNING id",
        dry_run_sql="SELECT COUNT(*) FROM processing_runs WHERE raw_item_id = ANY(:ids)",
        dry_run=dry_run,
        ids=ids,
    )

    # 9. normalized_item_revisions → CASCADE via normalized_items.deleted on normalized_items
    # so no explicit delete step is needed; we rely on FK CASCADE to clean them.
    counts["normalized_item_revisions"] = 0

    # 10. normalized_items —→ FK: raw_item_id (all RESTRICT referrers gone now)
    counts["normalized_items"] = _delete_ct(
        db,
        delete_sql="DELETE FROM normalized_items WHERE raw_item_id = ANY(:ids) RETURNING id",
        dry_run_sql="SELECT COUNT(*) FROM normalized_items WHERE raw_item_id = ANY(:ids)",
        dry_run=dry_run,
        ids=ids,
    )

    # 11. raw_item_source_payloads —→ FK: raw_item_id (CASCADE, manual scoping)
    counts["raw_item_source_payloads"] = _delete_ct(
        db,
        delete_sql="DELETE FROM raw_item_source_payloads WHERE raw_item_id = ANY(:ids) RETURNING id",
        dry_run_sql="SELECT COUNT(*) FROM raw_item_source_payloads WHERE raw_item_id = ANY(:ids)",
        dry_run=dry_run,
        ids=ids,
    )

    # 12. raw_items — root of the cascade
    counts["raw_items"] = _delete_ct(
        db,
        delete_sql="DELETE FROM raw_items WHERE id = ANY(:ids) RETURNING id",
        dry_run_sql="SELECT COUNT(*) FROM raw_items WHERE id = ANY(:ids)",
        dry_run=dry_run,
        ids=ids,
    )

    return counts


def affected_event_ids(db, raw_item_ids: list[int]) -> set[int]:
    return {
        int(value)
        for value in db.scalars(
            select(EventMention.event_id)
            .join(NormalizedItem, NormalizedItem.id == EventMention.normalized_item_id)
            .where(NormalizedItem.raw_item_id.in_(raw_item_ids))
        )
    }


def reconcile_affected_events(db, event_ids: set[int]) -> dict[str, int]:
    """Remove orphaned events and fully recompute retained event projections."""
    orphaned: set[int] = set()
    retained: set[int] = set()
    for event_id in event_ids:
        mentions = db.scalar(
            select(func.count())
            .select_from(EventMention)
            .join(EventMention.normalized_item)
            .where(EventMention.event_id == event_id, *current_event_mention_conditions())
        )
        if mentions:
            retained.add(event_id)
        else:
            orphaned.add(event_id)
    if orphaned:
        # Event is a derived current layer. An event without current mentions
        # cannot be retained merely because its historical memberships exist.
        db.execute(delete(EventMention).where(EventMention.event_id.in_(orphaned)))
        db.execute(
            text("DELETE FROM event_revisions WHERE event_id = ANY(:ids)"),
            {"ids": sorted(orphaned)},
        )
        db.execute(delete(Event).where(Event.id.in_(orphaned)))
    if retained:
        for event_id in retained:
            current_keys = {
                (
                    mention.normalized_item_id,
                    mention.normalized_item_revision,
                    mention.mention_index,
                    mention.aggregation_policy_version,
                )
                for mention in db.scalars(
                    select(EventMention)
                    .join(EventMention.normalized_item)
                    .where(
                        EventMention.event_id == event_id,
                        *current_event_mention_conditions(),
                    )
                )
                if mention.materiality == "material_update"
            }
            snapshots = [
                revision.evidence_snapshot or {}
                for revision in db.scalars(
                    select(EventRevision).where(EventRevision.event_id == event_id)
                )
            ]
            can_restore_projection = any(
                (
                    snapshot.get("normalized_item_id"),
                    snapshot.get("normalized_item_revision"),
                    snapshot.get("mention_index"),
                    snapshot.get("aggregation_policy_version"),
                )
                in current_keys
                and isinstance(snapshot.get("projection_snapshot"), dict)
                for snapshot in snapshots
            )
            if not can_restore_projection:
                raise RuntimeError(
                    f"event {event_id} has remaining mentions but no restorable projection; "
                    "refusing cleanup instead of leaving stale derived state"
                )
        # Rebuild every derived field from remaining mentions. This restores
        # summaries/key facts through revision snapshots instead of leaving a
        # count-only patch after removed evidence.
        refresh_event_metrics(db, retained)
    return {"events_deleted": len(orphaned), "events_rebuilt": len(retained)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform deletions. Without this flag the script runs a DRY-RUN.",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt (for automated runs).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    mode = "APPLY — will DELETE rows in the local lol_daily_intel DB" if args.apply else "DRY-RUN (read-only, no changes)"
    print(f"[mode] {mode}")
    _validate_local_database()

    with SessionLocal() as db:
        # 1. Find candidates (same SQL used for the eventual DELETE scope)
        candidates = find_candidates(db)
        print_candidates(candidates)
        if not candidates:
            print("\n✅ No cross-feed stray RawItems matched the filter. Nothing to do.")
            return 0

        raw_item_ids = list(int(c["raw_item_id"]) for c in candidates)
        # 2. Print downstream reference counts
        has_downstream = print_downstream_refs(db, raw_item_ids)

        # 3. Global before counts (for the delta-report + assertion)
        def _global_counts() -> dict[str, int]:
            return {
                "raw_items": int(db.scalar(select(func.count()).select_from(RawItem)) or 0),
                "normalized_items": int(
                    db.scalar(select(func.count()).select_from(NormalizedItem)) or 0
                ),
                "raw_item_source_payloads": int(
                    db.scalar(select(func.count()).select_from(RawItemSourcePayload)) or 0
                ),
                "daily_report_items": int(
                    db.scalar(select(func.count()).select_from(DailyReportItem)) or 0
                ),
                "event_mentions": int(
                    db.scalar(select(func.count()).select_from(EventMention)) or 0
                ),
                "event_aggregation_runs": int(
                    db.scalar(select(func.count()).select_from(EventAggregationRun)) or 0
                ),
                "normalized_item_revisions": int(
                    db.scalar(select(func.count()).select_from(NormalizedItemRevision)) or 0
                ),
                "normalized_item_media_extractions": int(
                    db.scalar(
                        select(func.count()).select_from(NormalizedItemMediaExtraction)
                    )
                    or 0
                ),
                "pipeline_corrections": int(
                    db.scalar(select(func.count()).select_from(PipelineCorrection)) or 0
                ),
                "pipeline_jobs": int(
                    db.scalar(select(func.count()).select_from(PipelineJob)) or 0
                ),
                "processing_checkpoints": int(
                    db.scalar(select(func.count()).select_from(ProcessingCheckpoint)) or 0
                ),
                "processing_runs": int(
                    db.scalar(select(func.count()).select_from(ProcessingRun)) or 0
                ),
                "review_tasks": int(
                    db.scalar(select(func.count()).select_from(ReviewTask)) or 0
                ),
            }

        pre_counts = _global_counts()
        print()
        print(
            "[pre-counts] "
            + "  ".join(f"{k}={v}" for k, v in sorted(pre_counts.items())[:5])
            + "  ..."
        )

        # 4. Compute the deletion plan before any destructive statement.
        plan = perform_cleanup(db, raw_item_ids, dry_run=True)

        if not args.apply:
            print("\n=== [DRY-RUN] Row deletion plan (NO CHANGES WRITTEN) ===")
            for table, n in plan.items():
                bar = "  " if n == 0 else " ⚠"
                print(f"  {table:<35} → {n:>5} rows would be deleted{bar}")
            print(
                "\nIf the list above looks correct, re-run with  --apply  to actually delete."
            )
            return 0

        # 5. Interactive confirmation (skip with --yes)
        if not args.yes:
            suffix = " AND cascade to their downstream rows" if has_downstream else ""
            expected_input = f"yes, delete {len(raw_item_ids)} raw items"
            prompt = (
                f"\nConfirm: delete {len(raw_item_ids)} RawItem(s){suffix}.\n"
                f"  Type exactly:  {expected_input}\n"
                f"  > "
            )
            resp = input(prompt).strip()
            if resp != expected_input:
                print("❌ Aborted. No changes made.")
                db.rollback()
                return 1

        # 6. Delete, rebuild affected Event projections, and validate before
        # committing. A failed invariant rolls back the whole maintenance run.
        affected_events = affected_event_ids(db, raw_item_ids)
        try:
            applied_plan = perform_cleanup(db, raw_item_ids, dry_run=False)
            if applied_plan != plan:
                raise RuntimeError("deletion scope changed after confirmation; refusing to commit")
            event_result = reconcile_affected_events(db, affected_events)
            db.flush()
            post_counts = _global_counts()
            remaining = _scalar(db, "SELECT COUNT(*) FROM raw_items WHERE id = ANY(:ids)", ids=raw_item_ids)
            if remaining != 0:
                raise RuntimeError(f"post-delete assertion failed: {remaining} candidate RawItems remain")
            expected_raw_total = pre_counts["raw_items"] - plan["raw_items"]
            if post_counts["raw_items"] != expected_raw_total:
                raise RuntimeError(
                    "post-delete assertion failed: raw_items total does not match the approved plan"
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
        print("\n=== [APPLY] Deletion delta per table ===")
        all_keys = sorted(set(pre_counts) | set(post_counts))
        for k in all_keys:
            pre, post = pre_counts[k], post_counts[k]
            delta = pre - post
            marker = ""
            if delta < 0:
                marker = " ⚠ WENT UP (unexpected!)"
            elif delta == 0 and k in plan and plan[k] > 0:
                marker = " ⚠ NO CHANGE even though plan expected some"
            print(f"  {k:<35} before={pre:>6} after={post:>6}  Δ=−{delta}{marker}")

        print("\n✅ Assertion 1/2: 0 candidate RawItem IDs remain in raw_items.")
        print(
            f"✅ Assertion 2/2: total raw_items dropped by exactly the plan count "
            f"(−{plan['raw_items']}). No unintended mass-delete."
        )
        print(
            f"✅ Event integrity: deleted={event_result['events_deleted']} "
            f"rebuilt={event_result['events_rebuilt']}."
        )

        print("\n🎉 Cleanup complete. Stray cross-feed RawItems and their downstream rows are gone.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
