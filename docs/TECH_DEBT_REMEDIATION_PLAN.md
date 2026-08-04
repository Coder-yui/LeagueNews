# LeagueNews Technical Debt Remediation Plan

Last updated: 2026-08-03

This is the durable execution record for evolving the running service into a reliable, traceable, and
replayable vertical event intelligence system. Existing architecture and public compatibility remain the
default. Existing migrations are an immutable ledger.

## Baseline

Repository state before remediation:

- Branch: `main` at `b1e6d68`.
- Pre-existing user changes preserved: `README.md`, `docs/LOCAL_RUNBOOK.md`, `scripts/start.sh`, and
  `scripts/stop.sh`.
- Ruff: passed.
- Frontend ESLint: passed.
- Frontend production build: passed.
- Backend: 127 passed, 1 skipped when proxy variables were removed. The skipped test is the opt-in
  PostgreSQL event concurrency test because `EVENT_TEST_DATABASE_URL` was not configured.
- Unmodified environment run: 24 failed, 103 passed, 1 skipped. All failures were caused by the host
  `ALL_PROXY=socks5://127.0.0.1:7897` being inherited by HTTPX while `socksio` was absent. This is an
  environment baseline failure, not an application regression.

Baseline command for deterministic local tests:

```bash
env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
  services/api/.venv/bin/python -m pytest services/api/tests -q
```

## Guardrails

- No production access, deployment, DNS, secrets, or production database operations.
- No destructive data operations and no mutation of migrations `002` through `031`.
- New schema changes are additive and backward-compatible.
- RawItem content remains immutable; event membership remains above NormalizedItem.
- PostgreSQL remains the durable queue and coordination mechanism.

## Milestone 1: collection reliability

- [x] Add persisted per-source cursor/watermark and overlap configuration.
- [x] Replace `last_success_at` as the content watermark.
- [x] Make connector fetch results report cursor, next cursor, truncation, and candidate count.
- [x] Advance cursor only after successful ingestion.
- [x] Continue/backfill capped batches without skipping the old cursor.
- [x] Preserve scheduled and run-now behavior.
- [x] Cover X, Weibo, and Riot: cap continuation, retry preservation, overlap deduplication, and boundaries.

## Milestone 2: recoverable and concurrency-safe pipeline

- [x] Add worker identity, lease expiry, heartbeat, recovery count, and recovery provenance to jobs.
- [x] Reclaim stale running jobs atomically and resume from formal state/checkpoint.
- [x] Add scheduler lease renewal and ownership checks during long connector runs.
- [x] Add database partial unique indexes for active item runs and pending item reviews.
- [x] Convert uniqueness races into idempotent results or explicit conflicts.
- [x] Exercise worker death/reclaim, double claim, manual/automatic races, concurrent event updates, and
  transient connector/LLM failures.

## Milestone 3: security boundaries

- [x] Split production environment/volume grants by API, pipeline worker, scheduler, and migrator.
- [x] Ensure pipeline worker has no platform cookies/profile and scheduler has no LLM key.
- [x] Redact credentials and signed URL queries from new HTTP/media error surfaces.
- [x] Resolve and validate all media host IPv4/IPv6 addresses and manually validate every redirect.
- [x] Document the remaining DNS rebinding window and allowlist hardening option.
- [x] Separate new raw/private media from published media without breaking legacy published URLs.
- [x] Add compatible CSP, Permissions-Policy, and related headers.
- [x] Document future application auth, CSRF/origin checks, RBAC, and audit triggers.

## Milestone 4: CI, observability, and reproducibility

- [x] Add PR/main quality jobs for Ruff, pytest, frontend lint/build, fresh DB, and ordered migration upgrade.
- [x] Gate image publishing on quality checks.
- [x] Persist workflow/prompt/schema/model/config/input/knowledge/glossary/response/usage/latency/retry/
  finish/error/commit/decision metadata without secrets.
- [x] Populate `knowledge_snapshot` with selected IDs and versions.
- [x] Add structured operational metrics for collection, queues, leases, stages, LLM, OCR, and media.
- [x] Update production documentation for `https://leaguenews.me` and external monitoring responsibilities.

## Milestone 5: prompt and feedback governance

- [x] Centralize versioned prompt metadata/schema contracts and separate fact extraction from importance.
- [x] Introduce `draft/evaluated/active/retired` KnowledgeRule lifecycle.
- [x] Make rejection and knowledge organization create draft candidates, never immediately active rules.
- [x] Preserve review input, model output, corrected value, and review provenance.
- [x] Add administrative list/evaluate/promote/retire operations through rule list/PATCH APIs.
- [x] Retrieve only text-matched glossary terms and context-relevant active rules.
- [x] Detect and report deterministic rule/glossary conflicts.
- [x] Add versioned offline evaluation fixtures, runner, machine metrics, human error report, and safe export
  workflow without fabricated labels.

## Milestone 6: controlled ontology, importance, and credibility

- [x] Add additive topic/facet/ontology fields while preserving legacy `category` and `entities`.
- [x] Constrain entity types and retain `other/unknown` instead of inventing canonical identities.
- [x] Replace model-authored final importance with five 0–4 dimensions and deterministic weighted policy.
- [x] Preserve the legacy `importance_score` projection and record dimensions, weights, floor/cap, and version.
- [x] Separate source prior, repost status, evidence quality, OCR, and translation components from event
  corroboration; group reposts by upstream URL when available.

## Milestone 7: lightweight Claims and bounded event recall

- [x] Add traceable, revisioned Claims with raw block evidence and a many-to-many EventClaim bridge.
- [x] Keep EventMessage as the compatibility membership projection.
- [x] Create a conservative single Claim during publication and provide a dry-run-by-default backfill tool.
- [x] Bound event candidate reads with indexed key/category/time filters before hybrid scoring.
- [x] Preserve candidate feature/reason snapshots and add Recall@5/false merge/false split evaluation metrics.

## Milestone 8: digest, feeds, and read-only MCP distribution

- [x] Generate idempotent daily/weekly digests from EventRevision windows with cutoff and timezone.
- [x] Create a new DigestRevision for late information instead of silently rewriting provenance.
- [x] Add public digest APIs/pages plus events and digest RSS feeds with stable revision GUIDs.
- [x] Add the six required read-only MCP tools with structured content and source provenance.
- [x] Implement MCP against the current stable `2025-11-25` protocol revision; no mutation tools exist.

## Docker and resource controls

- [x] Record a read-only local image/volume/cache baseline without pruning.
- [x] Document shared image layers and the remaining API/Chromium/OCR layer measurement.
- [x] Keep image splitting and media cleanup deferred until references and measured benefits are proven.

## Completion gate

- [x] All existing and new deterministic tests pass without real platform credentials or LLM keys.
- [x] PostgreSQL migration tests cover fresh initialization and a 031-to-latest path.
- [x] Ruff, frontend lint, and frontend production build pass.
- [x] Compose configuration validates without exposing unrelated secrets to services.
- [x] Documentation matches implementation and remaining operational limitations are explicit.

## Final verification record

- Ruff: passed.
- Backend SQLite/fixture suite: 148 passed; 2 PostgreSQL-only tests skipped in that invocation.
- PostgreSQL concurrency suite: 2 passed (event revision/membership and worker/manual-auto coordination).
- Fresh PostgreSQL initialization: passed with all 36 migration versions (002–037) recorded.
- PostgreSQL 031 fixture upgrade: migrations 032–037 applied successfully in order.
- Frontend ESLint and Next.js production build: passed.
- Production Compose render and Caddy config validation: passed.
- Offline evaluation runner: 3/3 explicit regression fixtures matched.
