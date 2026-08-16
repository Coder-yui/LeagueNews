# Event Aggregation

> Status: Event Aggregation V2 implemented
>
> Policy version: `event-aggregation-v11-match-semantic-continuation`

Event aggregation answers one question for each meaningful mention in a published
`NormalizedItem`: attach it to a recalled `Event`, create a new `Event`, or ignore it.

The current membership contract and validation boundary are documented in
[EVENT_AGGREGATION_V2.md](EVENT_AGGREGATION_V2.md); filter and recall details are in
[EVENT_ADMISSION_AND_GRANULARITY.md](EVENT_ADMISSION_AND_GRANULARITY.md). Refactor notes and
real-data evaluation results are historical records under [`history/`](history/README.md).

## Runtime flow

```text
published NormalizedItem
  -> minimal_event_filter()             # process / skip
  -> products/topics event-space routing
  -> recall_event_candidates()          # product/family gated, bounded high recall
  -> LLM aggregate_events()             # one logical message-level call
  -> structural candidate validation
  -> apply_membership_transaction()
  -> refresh_event_metrics()
```

写入 membership 时同步刷新投影；时间相关的热度衰减由 Pipeline Worker 周期刷新。公开 GET API
只读取投影，不提交数据库事务。

Published `NormalizedItem` revisions enter Event aggregation through the existing
`PipelineJob` queue. The Pipeline Worker is the single downstream owner; message
approval does not synchronously call the Event LLM.

`event_id` is the only Event identity. `canonical_anchors` are optional descriptive and recall
metadata; they are not a parallel identity mechanism.

## Invariants

- Only the latest published item revision is aggregated.
- A completed run is idempotent per item revision and aggregation policy version.
- At most one `running` `EventAggregationRun` is allowed for the same
  `normalized_item_id + normalized_item_revision`; the partial unique index fences
  concurrent and stale workers without blocking a newer revision.
- A mention is unique per item revision, mention index and aggregation policy version.
- Attach can reference only an Event in the bounded candidate payload.
- `esports_match` uses **continuation-first**: once a match has entered this family, its score /
  result / live→finished state updates are `material_update`s of the **same** Event and must not
  `create` a new one. A new Event is allowed only for a genuinely different occurrence. Python
  (the model business validator and the apply-time fence) rejects a continuation-`create` only on a
  **strong positive same-occurrence evidence** — an equal explicit `external_match_id` (decisive on
  its own, even without participants), participants plus an equal explicit `match_date`, participants
  plus an equal explicit `scheduled_at` (full datetime, never a bare date), or participants plus
  equal `competition`/`stage`/`round` — with no hard occurrence conflict. This structured evidence is
  a **deterministic guard threshold, not the LLM's attach threshold**: the model may still attach an
  ambiguous candidate by **semantic continuation evidence** (equal participants + a continuous
  lifecycle state — score advancing, live→finished, winner resolved — + a recent candidate + agreeing
  competition/stage context), even when no strong structured fact is present. Absence of a hard
  conflict is **never** treated as proof of the same occurrence, and same participants alone are never
  enough; if occurrence information is insufficient, the model creates only when it has positive
  reason this is a new match. The apply layer keeps a hard fencing conflict guard (explicit
  `match_date`, `scheduled_at`, `external_match_id`, `stage`, or `round` conflicts reject attach after
  the model decision and again before membership is written; missing identity fields are not
  conflicts; one-sided `match_date` vs `scheduled_at` can still prove a date-level conflict but never
  exact `scheduled_at` equality). An `esports_match` Event represents one concrete match or series
  occurrence, not the recurring relationship between its participants. Known occurrence metadata is
  stored in `canonical_anchors`; it remains descriptive membership metadata and never replaces
  `event_id`. Same participants are a recall signal only, never a deterministic proof.
- `esports_schedule` carries pre-match fixtures / schedules / opening arrangements and may be a
  separate Event from the in-progress `esports_match`. Once a match actually begins and pushes
  state, it belongs to `esports_match` and those states must continue the same Event.
- Candidate retrieval is family-aware and applies the routed-family gate **in SQL before the
  bounded candidate limit**: `esports_match` recalls within the recent **7 days**; other families
  keep the 365-day recall window. The 7-day bound is a search boundary only, never a match identity
  rule.
- Candidate retrieval hard-gates explicit products and routed event families; entities and text
  overlap only rank candidates within that space.
- `possible_event_families` is derived from upstream `products + topics`; the model cannot create
  or attach a family outside that routed space.
- Create requires a minimal title and summary but no deterministic identity shape.
- All membership writes for one model result commit atomically or roll back together.
- A `running` aggregation run is still an active-ownership signal while its
  `updated_at` is within the configured Pipeline Worker lease (5 minutes by default). A later invocation exits without an LLM call;
  only an older run is reclaimed. If the persisted stage is `apply_membership`,
  the saved `decision_draft` is replayed instead of calling the model again. The
  idempotency key and database uniqueness constraints remain authoritative.
- `repost` cannot create an Event. This is a deterministic application invariant enforced by
  `_validate_repost_actions()`; the model prompt repeats it as guidance, but is not the authority.
- Source evidence, relation, source role, materiality and source reliability are preserved on
  `EventMention`.
- A public EventMention is current only when its NormalizedItem is published and
  `EventMention.normalized_item_revision == NormalizedItem.current_revision`. Historical mentions
  remain stored for audit, but metrics, counts, references, detail/timeline, event listing and
  report deduplication use only current mentions.
- Each material-update `EventRevision` records a **mention-specific projection patch**: only the
  presentation fields that this mention actually provided (`title`, `current_summary`,
  `latest_development`, `lifecycle_status`, `canonical_anchors`, `key_facts`). It never stores a
  whole-Event snapshot taken after processing, so a late-reprocessed older message can never bake
  the newest global projection into its own revision. On invalidation the projection is **rebuilt
  from a clean baseline** by replaying only the still-valid material-update patches in evidence
  order (`evidence_time`, then `EventMention.id`) — not by max-revision selection and never by
  incrementally overriding a stale `Event` row. A baseline cleared at the start of restore guarantees
  that evidence-derived fields left by an invalidated mention (`canonical_anchors`, `key_facts`,
  `latest_development`, `lifecycle_status`) cannot survive; stable non-nullable columns (`title`,
  `current_summary`) fall back to empty strings when no valid patch restores them. Legacy revisions
  written before these patches fall back to their stored full snapshot when available.
- Importance, credibility, heat, references and presentation are projections refreshed after
  membership; they do not choose or reject membership.
- `material_update` is the only materiality that carries a new development. Every `material_update`
  attach must provide a projection that at least sets `latest_development`, so that
  `latest_update_message_id` / `last_material_update_at` / `latest_development` all point at the same
  latest still-valid material mention. `corroboration_only`, `duplicate` and `context_only` attaches
  must not carry a projection and never advance those three fields.

## Evaluation

Deterministic engineering behavior lives in pytest. Semantic cases live in
`services/api/evals/event_aggregation_v2_cases.json` and can be scored with:

```bash
services/api/.venv/bin/python services/api/scripts/evaluate_event_aggregation.py predictions.json
```
