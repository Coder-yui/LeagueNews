# Intelligence data and distribution

The compatibility path remains `RawItem -> NormalizedItem -> EventMessage -> Event`. New projections are
additive:

```text
RawItem immutable blocks
  -> NormalizedItem (facts, classification, entities, provenance, importance)
  -> Claim (raw block evidence, revision, provenance)
  -> EventClaim (a Claim may support multiple Events)
  -> EventRevision
  -> Digest + DigestRevision
  -> public API/pages and RSS
  -> read-only MCP tools
```

`ontology_version=lol-news-v6` controls primary topic, secondary topics, facets, and entity types. Existing
rows are mapped conservatively by migration 037; unknown identities remain unknown. No LLM runs in a
migration. `python -m scripts.backfill_claims` is dry-run by default; after a database/media backup and
review, `--apply` creates one evidence-linked Claim for each published item that has no active Claim and
also restores missing EventClaim links for active EventMessage memberships. The command reports Claim and
EventClaim counts separately and is idempotent. Withdrawn items and memberships are never re-linked.

Importance analysis extracts controlled scale, audience, competition-region, prominence, skin-tier, and
bulk-update features. `importance-v7-cosmetic-releases` computes intrinsic importance from versioned
editorial baselines, bounded modifiers, and subtype score bands. New skins, standalone new chromas, and
CN-exclusive paid chromas share the cosmetic-release band; guaranteed free-skin claim reminders use the
high-value free-skin activity band. Information stage, repost form, and non-CN-only scope affect the
separate message-priority projection, not intrinsic importance. Source reliability never increases
importance. Event credibility is a separate deterministic projection and deduplicates reposts by upstream
URL when present.

Daily and weekly digests validate an IANA timezone, interpret naive cutoffs as local wall time, calculate
one or seven local days (including DST transitions), and then persist/query the UTC window. Titles use the
local cutoff date. Re-running the same normalized window is idempotent; late evidence creates a
DigestRevision. Feeds expose published events and digests only. MCP is a read-only Streamable HTTP
JSON-RPC endpoint at `/api/v1/mcp`, implementing the
current stable protocol revision `2025-11-25`; Caddy keeps this POST endpoint behind administrator
authentication. It exposes `list_events`, `get_event`, `get_event_timeline`, `search_events`,
`list_digests`, and `get_digest`. Event Claim timelines preserve active, superseded, and withdrawn Claims,
including their replacement links, attribution, evidence, and EventClaim relation.

Remaining operational validation: schedule digest generation at the desired local cutoffs, run the Claim
backfill only after backup, validate feed discovery in production, and test the MCP client through the
production reverse proxy. DNS, TLS, authentication, and production data were not touched by this work.

## Optional Agent Skill

This application repository is not a Codex skill installation directory, so no runtime skill bundle is
forced into it. A future small LeagueNews skill should connect to the MCP endpoint and instruct an Agent to:

- use event search/list for current topics, event detail/timeline for claims and source provenance, and
  digests only for window summaries;
- describe importance as a deterministic prioritization policy and event credibility as deterministic
  source/evidence corroboration, and never present either as certainty;
- cite the returned original source URLs and distinguish a Claim from an Event editorial summary;
- never expose or request mutation, deletion, review, or production administration tools.

Install that skill only in the intended Agent environment after the production MCP endpoint and its
administrator authentication have passed client interoperability testing.
