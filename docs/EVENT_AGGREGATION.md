# Event Aggregation

> Status: Event Aggregation V2 implemented
>
> Policy version: `event-aggregation-v6-lifecycle-cohesion`

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

`event_id` is the only Event identity. `canonical_anchors` are optional descriptive and recall
metadata; they are not a parallel identity mechanism.

## Invariants

- Only the latest published item revision is aggregated.
- A completed run is idempotent per item revision and aggregation policy version.
- A mention is unique per item revision, mention index and aggregation policy version.
- Attach can reference only an Event in the bounded candidate payload.
- Candidate retrieval hard-gates explicit products and routed event families; entities and text
  overlap only rank candidates within that space.
- `possible_event_families` is derived from upstream `products + topics`; the model cannot create
  or attach a family outside that routed space.
- Create requires a minimal title and summary but no deterministic identity shape.
- All membership writes for one model result commit atomically or roll back together.
- `repost` cannot create an Event. This is a deterministic application invariant enforced by
  `_validate_repost_actions()`; the model prompt repeats it as guidance, but is not the authority.
- Source evidence, relation, source role, materiality and source reliability are preserved on
  `EventMention`.
- Importance, credibility, heat, references and presentation are projections refreshed after
  membership; they do not choose or reject membership.

## Evaluation

Deterministic engineering behavior lives in pytest. Semantic cases live in
`services/api/evals/event_aggregation_v2_cases.json` and can be scored with:

```bash
services/api/.venv/bin/python services/api/scripts/evaluate_event_aggregation.py predictions.json
```
