# Event Aggregation

> Status: Event Aggregation V2 implemented
>
> Policy version: `event-aggregation-v8-match-time-boundary`

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
- An `esports_match` Event represents one concrete match or series occurrence, not the recurring
  relationship between its participants. Explicit `match_date`, `external_match_id`, `stage`, or
  `round` conflicts reject attach after the model decision and again before membership is written.
  The same model response extracts explicit candidate facts to cover older Events whose anchors are
  incomplete. Missing identity fields are not conflicts. Known occurrence metadata is stored in
  `canonical_anchors`; it remains descriptive membership metadata and never replaces `event_id`.
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
- Material EventRevision evidence snapshots preserve the current title/summary and related
  projection fields. When a NormalizedItem revision is invalidated, the projection is restored
  from the newest still-valid snapshot and all derived metrics/references are recomputed. Legacy
  revisions created before these snapshots do not contain enough information to rebuild a changed
  title or summary reliably; the stable label is retained while lifecycle, counts, references and
  metrics are reset from the remaining evidence.
- Importance, credibility, heat, references and presentation are projections refreshed after
  membership; they do not choose or reject membership.

## Evaluation

Deterministic engineering behavior lives in pytest. Semantic cases live in
`services/api/evals/event_aggregation_v2_cases.json` and can be scored with:

```bash
services/api/.venv/bin/python services/api/scripts/evaluate_event_aggregation.py predictions.json
```
