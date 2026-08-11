# LeagueNews Repository Guide

## Structure

- `services/api/app`: FastAPI application, SQLAlchemy models, connectors, workflows, and services.
- `services/api/tests`: backend unit and opt-in PostgreSQL integration tests.
- `apps/web`: Next.js public site and administrator console.
- `infra/postgres/migrations`: append-only PostgreSQL migration ledger.
- `deploy`: production Compose, Caddy, deployment, backup, and restore tooling.
- `docs`: architecture, operations, development, and production runbooks.

## Development and validation

Run from the repository root:

```bash
services/api/.venv/bin/python -m ruff check services/api/app services/api/scripts services/api/tests
services/api/.venv/bin/python -m pytest services/api/tests -q
pnpm lint:web
pnpm build:web
```

PostgreSQL-only tests require an explicitly disposable test database. External platform tests must use
fixtures or mocks unless a human intentionally runs the separate live smoke test.

## Architecture invariants

- A Connector is a platform capability; a Source is a specific account or site.
- Connectors only fetch platform data and map it to `RawItemCandidate`.
- Shared ingestion owns validation, deduplication, media storage, provenance, RawItem persistence, and
  downstream job enqueueing.
- `raw_items.content_blocks` is immutable source evidence. Processing and review code must never rewrite it.
- `normalized_items` is the current publication projection; revisions preserve publication history.
- Events are a separate layer above NormalizedItem. RawItem must not store event membership.
- Automatic and manual paths share proposals, schema/business validation, and checkpoints.
- Existing SQL migrations are immutable history. Add a new numbered migration; never edit, rename, or
  delete an existing migration.

## Data safety

- Preserve unrelated working-tree changes.
- Never read, print, copy, or commit production environment files, cookies, tokens, passwords, or headers.
- Never use `git reset --hard`, `git clean`, or `docker compose down -v`.
- Never mutate production databases.
For a verified local development database, a downstream-processing reset is allowed
only with explicit user authorization. Before resetting, stop workers, verify the
exact database host and name, and preserve Sources, RawItems, source payloads,
original media assets, rules, and glossary terms.
A local downstream reset may remove pipeline jobs, corrections, review tasks,
processing checkpoints, processing runs, normalized-item media associations,
normalized-item revisions, normalized items, and derived media extractions.
It must never remove or rewrite RawItems or original source evidence.
- Do not connect to or mutate production while developing or testing.
- Database changes must be backward-compatible and migration-driven.

## Before completing a change

- Review `git diff` and confirm no existing migration or unrelated user file was overwritten.
- Run Ruff and the relevant backend tests; run the full backend suite for cross-cutting changes.
- Run frontend lint and production build for frontend or API-contract changes.
- Validate fresh database initialization and ordered upgrades when models or migrations change.
- Recheck RawItem immutability, idempotency, concurrency constraints, secret isolation, and public API
  compatibility for changes that touch those boundaries.
