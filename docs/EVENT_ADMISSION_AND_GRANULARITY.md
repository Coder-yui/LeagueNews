# Event Filtering, Recall, and Granularity

> Policy version: `event-aggregation-v12-identity-gate-subject-continuation`

## Minimal filter

The filter returns only `process` or `skip`. It skips an unpublished item or an item with no usable
semantic text. It does not decide whether an item may create an Event and contains no business
regex for free champions, shops, esports, patches, reposts, leaks or promotions.

## Candidate recall

Recall is a bounded high-recall operation. It looks at recent Events and ranks them with generic
signals: topic-to-family hints, product overlap, entity overlap, lightweight title/summary lexical
overlap and recent activity. The routed event-family gate and its recall lookback are applied **in
SQL before the bounded candidate limit**: `esports_match` searches the recent **7 days**, while other
families keep the 365-day window. Applying the family filter first means unrelated families cannot
consume the bounded candidate budget and starve the family that actually needs recall. This boundary
is a search limit only — never a match identity rule, and message publication time is never treated
as match identity. Product or anchor differences are not hard conflicts. Candidate metadata is
context for the model and never proof of identity.

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

### Esports match occurrence boundary

`esports_match` is one concrete match or series occurrence. A preview, live update and result for
that occurrence belong to the same Event, but the same participants meeting again belong to a new
Event. The model considers participants, competition, stage/round, match date or scheduled time,
series format and an official external match ID when available. Participants alone do not establish
identity, and projection updates cannot turn an old match Event into a later match.

Once a match has entered `esports_match`, membership is **continuation-first**: score changes,
winner becoming known, live→finished transitions and advancement/elimination results are
`material_update`s of the same Event, not reasons to `create`. A new Event is justified only by a
genuinely different occurrence (explicitly different match date, external match ID, stage, round or
scheduled time) or by the absence of a reasonably compatible candidate. A model `create` is rejected
as a business validation error — and the concrete reason is returned to the model through the retry
loop — only when there is **strong positive same-occurrence evidence** (equal explicit external
match ID; participants plus an equal explicit match date; or participants plus equal
competition/stage/round; with no hard conflict). Absence of a hard conflict is never treated as
proof of the same occurrence: two same-participant matches with no recorded occurrence facts are
left to the model's semantic judgment, not force-merged. `esports_schedule` may carry the pre-match
fixture separately.

The workflow records explicit match identity facts in `canonical_anchors` and applies a narrow hard
conflict guard to attach decisions. After `esports_match` candidate recall and before the LLM
decision, an **identity gate** drops candidates with a hard identity conflict against the incoming
message identity (explicitly different participants, external match ID, match date, scheduled time,
stage or round; a one-sided missing field is unknown, not a conflict). Candidate identity is read
only from the system-stored `canonical_anchors`; the model never re-declares or back-fills candidate
identity, so an identity-deficient older candidate stays unknown instead of being patched by the
model. When both sides provide incompatible match dates, external match IDs, stages or rounds, attach
is rejected. A field missing on either side is not a hard conflict and the semantic decision remains
with the model. Message publication timestamps are never treated as match dates.

An `esports_match` Event needs a valid title and a recognizable match subject (participants or an
external match ID) to exist as a normal Event. A shell Event with an empty title or no usable match
subject is never recalled as an attach candidate, and repair/rebuild deletes such orphan/invalid
Events instead of leaving an empty shell. `esports_match` create requires the same recognizable
match subject; an empty `match_identity` fails business validation.

`latest_development` (together with `last_material_update_at` and `latest_update_message_id`) is
derived from the newest still-valid material update by evidence time, not from processing order or
the largest revision, so a late-reprocessed older message never rolls the projection backward.

Family-specific examples are evaluation data, not Python policy. See
`services/api/evals/event_aggregation_v2_cases.json`.
