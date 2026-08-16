# Event Aggregation V2 Contract

## Message Processing → Event Aggregation

`NormalizedItem` is an observation whose semantic fields are owned by Message Processing. Event
Aggregation consumes `products`, `message_type`, `topics`, `entities`, `content_form`, normalized
title/summary/text, source metadata and upstream importance. It does not reclassify the message.

The event-layer contract is:

```text
published NormalizedItem
  → minimal process/skip filter
  → products + topics → possible event families
  → product/family/entity/recency candidate retrieval (bounded)
  → one LLM semantic coreference decision
  → structural validation + esports_match occurrence conflict guard
  → esports_match continuation-first create/attach business validation
  → atomic Event/EventMention membership persistence
  → importance, credibility, heat and presentation projections
```

The LLM chooses `attach`, `create`, or `ignore` for each meaningful mention. It may provide
evidence and optional presentation updates, but it does not output product/topic/message-type
classification, deterministic event identities, market/week keys, match keys or numeric identity
signatures. For `esports_match`, it also extracts optional occurrence facts for the current mention
and, on attach, the candidate: participants, competition, stage/round, match date, scheduled time,
series format and an official external match ID. Candidate extraction can fill metadata absent from
older Events. These facts are compatibility metadata in `canonical_anchors`, not a second Event
identity.

Before that decision, the model groups the whole message by independent real-world lifecycle. A
shared release batch, version or series, launch window, status and follow-up path forms one group;
its enumerated subitems remain `key_facts`. A separately named release with its own status and
follow-up path may form another group. This same semantic rule applies to every message type.

## Cross-product messages

`NormalizedItem.products` describes the products materially covered by the whole message. It is not
copied wholesale onto every Event. Each non-ignored mention carries one `product` and is persisted
as a product-isolated Event membership. A cross-product message can therefore create, attach, and
ignore mentions independently in the `lol_pc` and `tft` domains. An attach candidate must belong
to exactly the selected product; existing multi-product Events from older runs are not treated as
isolated candidates. A single-product message may omit the mention product because the workflow
derives it unambiguously.

## Routing contract

`possible_event_families(products, topics)` is a small taxonomy map. Products provide the hard
Event Space gate; topics narrow it to compatible families. Entity values are retrieval features,
not identity proofs. Current real-data product values are `lol_pc`, `tft`, `lol_esports`,
`lol_universe`, `other_lol_product`, `riot_ecosystem` and `unknown`; current entity values are
primarily teams, players, champions and leagues.

Candidate recall applies product and routed-family gates before ranking. Within that bounded set it
uses entity overlap, lightweight title/summary overlap and recency. It never proves that two
observations are the same Event.

`message_type` remains upstream semantic context. The LLM uses it, together with source metadata
and candidate mentions, to select relation, source role and materiality. Projection code derives
credibility/lifecycle, heat and importance after membership; no projection can redirect
membership.

## Python validation boundary

Python verifies schema, contiguous mention indexes, candidate membership, routed family
compatibility, evidence presence, idempotency, model-call audit, transaction atomicity and
projection refresh. It does not impose message-type or event-family mention limits or generally
verify semantic equivalence. The two narrow `esports_match` exceptions:

- **Attach guard (apply-time fence).** When both the mention and candidate state incompatible
  match dates, external match IDs, stages or rounds, Python rejects the attach. Missing
  occurrence fields remain a semantic model decision.
- **Continuation-first create guard (model business validation).** For `esports_match`, a
  `create` is rejected before persistence only when there is exactly one compatible candidate with
  **strong positive same-occurrence evidence** — an equal explicit `external_match_id` (decisive on
  its own, even without participants), participants plus an equal explicit `match_date`, participants
  plus an equal explicit `scheduled_at` (full datetime, never a bare date), or participants plus
  equal `competition`/`stage`/`round`; and no hard occurrence conflict. This is a *business
  validation error* fed back to the model through the retry loop. It is never a deterministic forced
  attach, and *absence of a conflict is never proof of the same occurrence*: same-participant
  messages with no conflict are left to the model's semantic judgment. Crucially, this strong
  structured evidence is the **Python deterministic guard threshold, not the LLM's attach
  threshold**; when evidence is ambiguous (zero or multiple strong-evidence candidates) the LLM
  keeps full semantic control and may still attach via semantic continuation.

## esports_match continuation-first

Once a match has entered `esports_match`, its observed state is a single concrete occurrence's
lifecycle. `0:0 → 1:0 → 1:1 → 2:1 → finished → final result` must remain **one** Event. Score
changes, winner becoming known, live→finished transitions and advancement/elimination results are
`material_update`s of the existing match, never reasons to `create`. The prompt teaches the model
to attach score/result/winner/advancement updates to the existing match, while two different
recorded occurrences of the same two teams must `create` a new Event. The only conditions that
deterministically justify a new `esports_match` Event are a genuinely different occurrence —
explicitly different `match_date`, `stage`, `round`, `scheduled_at` or `external_match_id` — or the
absence of any reasonably compatible existing candidate.

Python enforces continuation-first **only** on strong positive same-occurrence evidence. There is no
score-regex or Chinese keyword parser driving it: a create is rejected either because a strong fact
proves this message continues an existing match, or it is left to the LLM. Formats like `2-1`, `2:1`
or a natural-language result ("BLG 让一追二击败 TES") are treated identically because validation
depends on structured occurrence facts, not its wording.

- `esports_schedule` carries pre-match fixtures, schedules and opening arrangements, and may be a
  separate Event from the in-progress match. Once a match actually begins and pushes state, it
  belongs to `esports_match` and subsequent score/result/advancement states must continue that
  Event.
- The Python strong-evidence guard is deliberately strict and conservative. The LLM's **attach**
  judgment is intentionally looser: it may conclude two mentions are the same match from **semantic
  continuation evidence** — equal participants plus a continuous lifecycle (score advancing,
  live→finished, winner resolved) plus a recent candidate plus agreeing competition/stage context —
  even when no strong structured fact is present, or when only participants are shared but the
  lifecycle state is clearly continuous. Same participants alone are never enough, and if occurrence
  information is insufficient the model creates a new Event only with positive reason; it must not
  `create` merely because a strong structured fact is absent.
- `match_identity`/`candidate_match_identity` are occurrence-compatibility metadata inside
  `canonical_anchors`; they are used for hard-conflict fencing, strong same-occurrence evidence and
  model context, not a second Event identity. `event_id` remains the only identity. `match_date`
  compares by date; `scheduled_at` (when both sides provide it) compares by full normalized datetime,
  so two matches on the same day at different times are distinct occurrences. A one-sided
  `match_date` vs a one-sided `scheduled_at` may prove a date-level conflict but never exact
  `scheduled_at` equality.
- `esports_match_same_occurrence_evidence()` is the single helper deciding whether Python may
  deterministic-reject a continuation `create`. It is deliberately conservative and never promotes
  "no conflict" to "same match".

## Candidate recall by family

Candidate retrieval is family-aware and applies the routed-family gate **in SQL before the bounded
candidate limit**: `esports_match` uses a recent **7-day** search boundary (`last_seen_at`), while
every other family keeps the original recall window (365 days). Because the family filter and its
window are applied in the SQL query that precedes `ORDER BY ... LIMIT 500`, unrelated families
cannot consume the bounded candidate budget and starve the family that actually needs recall. The
7-day bound and its recency score are a *search boundary only* — never a match identity rule. Two
clearly distinct matches inside 7 days still create two Events; one match is never merged across
the boundary. Message publication time is never treated as match identity.

## latest_development projection

`latest_development`, `last_material_update_at` and `latest_update_message_id` all point at the
same thing: the latest **still-valid material_update by evidence time** with a deterministic
tie-break (`evidence_time`, then `EventMention.id`). This is not the last-processed message nor the
largest revision. A late-reprocessed older message receives a higher `EventRevision.revision` but an
older evidence time, so the projection restore replays the still-valid material-update patches in
evidence order (never the max revision), and a revision invalidation can deterministically rebuild
the correct projection.

Each material-update `EventRevision` records its **own mention-specific projection patch** — only
the presentation fields that mention contributed. It never stores a whole-Event snapshot taken after
processing, so a message reprocessed late cannot bake the then-current global projection into its
own revision and later resurrect the wrong state. On invalidation, restore:

1. clears a **clean projection baseline** (`title`/`summary`/`latest_development`/`lifecycle_status`/
   `canonical_anchors`/`key_facts` back to empty defaults) so evidence left by an invalidated mention
   can never survive;
2. collects the still-valid material-update mentions;
3. looks up each mention's recorded projection patch;
4. replays the patches in evidence order (stable tie-break), applying only the fields present.

`corroboration_only`, `duplicate` and `context_only` mentions never advance
`latest_development`, `last_material_update_at` or `latest_update_message_id`.

## material_update projection contract

`latest_development`, `last_material_update_at` and `latest_update_message_id` all point at the
same thing: the latest **still-valid material_update by evidence time** with a deterministic
tie-break (`evidence_time`, then `EventMention.id`). To keep that guarantee, every `material_update`
attach must provide a projection that at least sets `latest_development` — each real development
must describe what happened — while `corroboration_only` / `duplicate` / `context_only` attaches
must not carry a projection and never advance these three fields.

## Removed duplicate NLP

The retired event-identity parsers were deleted. Their regexes and deterministic
parsers for weekly rotations, mythic-shop market/week, esports team pairs, relative dates, patch
signatures and strong-anchor identity are not part of Event Aggregation. The score-regex
(`_MATCH_SCORE_PATTERN`) and Chinese match-state keyword table (`_MATCH_STATE_TERMS`) are removed
too: continuation-first membership is decided by structured occurrence facts, never by parsing score
formats or natural-language result keywords, so `2:1`, `2-1` and "让一追二击败" are handled
identically. Remaining regexes belong to upstream Message Processing/OCR or generic token overlap
and are not event identity rules. The aggregation path does not use upstream hotfix signals or
message-type branches to determine Event count or identity. The `esports_match` guard compares
model-extracted structured facts, not parsed team names or match dates derived from message
publication timestamps.

## Evaluation boundary

Engineering invariants live in deterministic pytest tests. Real cases such as roster rumors,
match lifecycle, recurring shop rotations, patch leaks, corrections and multi-mention messages
live in `services/api/evals/event_aggregation_v2_cases.json`; the offline scorer never mutates the
database. Evaluation failures should change upstream fields, routing, recall, prompt/context or
model examples before adding production special cases.
