# Event Filtering, Recall, and Granularity

> Policy version: `event-aggregation-v6.1-recall60-latest-evidence`

## Minimal filter

The filter returns only `process` or `skip`. It skips an unpublished item or an item with no usable
semantic text. It does not decide whether an item may create an Event and contains no business
regex for free champions, shops, esports, patches, reposts, leaks or promotions.

## Candidate recall

Recall is a bounded high-recall operation. It looks at Events from the last 60 days and ranks them
with generic signals: topic-to-family hints, product overlap, entity overlap, lightweight
title/summary lexical overlap and recent activity. The 60-day boundary only limits which Events are
offered as candidates; it is not an event identity rule. Product or anchor differences are not hard
conflicts. Candidate metadata is context for the model and never proof of identity.

## Semantic granularity

The model applies these principles:

- observations about the same core subject, real-world development and lifecycle attach to one
  Event;
- products, rewards, components and subcontent normally remain within the parent Event;
- an independently updating development with its own lifecycle may become a separate Event;
- a roundup can emit multiple mentions without creating a generic roundup Event;
- recall candidates may be wrong, so attach is never forced.

Granularity is one semantic rule, independent of message type: first group the message by real-world
lifecycle, then emit at most one mention per group. Items that share a release batch, version or
series, launch window, status and follow-up path remain `key_facts` of one Event. This covers fixes,
routine balance changes and a same-batch skin lineup without encoding those examples as Python
policy. A separately named release with its own status and follow-up path may become another Event.
Python validates structure and routing, not semantic identity or a message-type-specific count.

### Esports match time semantics

An `esports_match` Event is one concrete match: its start, score progress, in-match developments,
final result and outcome normally happen on the same day or within a very short window, so they are
preferred as continued development (`material_update`) of the same Event rather than new Events.
The time distance between the message and a candidate match is an important semantic signal the
model weighs together with participants, titles, summaries and competition context: a clearly long
gap — especially across days — in a context that reads like another meeting of the same two teams
justifies creating a new Event. Time is guidance for the model's semantic judgment, not a Python
threshold: identical participants do not imply the same Event forever, and a score change alone
never implies a new Event.

`latest_development` (with `last_material_update_at` and `latest_update_message_id`) is derived
from the newest still-valid material update by message evidence time (`published_at`, falling back
to `ingested_at`), never from processing order or revision size, so a late-reprocessed older
message never rolls the projection backward.

Family-specific examples are evaluation data, not Python policy. See
`services/api/evals/event_aggregation_v2_cases.json`.
