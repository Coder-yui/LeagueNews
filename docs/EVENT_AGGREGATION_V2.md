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
  → structural validation + esports match occurrence conflict guard
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
verify semantic equivalence. The narrow exception is `esports_match`: when both the mention and
candidate state incompatible match dates, external match IDs, stages or rounds, Python rejects the
attach. Missing occurrence fields remain a semantic model decision.

## Removed duplicate NLP

The retired event-identity parsers were deleted. Their regexes and deterministic
parsers for weekly rotations, mythic-shop market/week, esports team pairs, relative dates, patch
signatures and strong-anchor identity are not part of Event Aggregation. Remaining regexes belong
to upstream Message Processing/OCR or generic token overlap and are not event identity rules. The
aggregation path does not use upstream hotfix signals or message-type branches to determine Event
count or identity. The `esports_match` guard compares model-extracted structured facts; it does not
parse team names or derive match dates from message publication timestamps.

## Evaluation boundary

Engineering invariants live in deterministic pytest tests. Real cases such as roster rumors,
match lifecycle, recurring shop rotations, patch leaks, corrections and multi-mention messages
live in `services/api/evals/event_aggregation_v2_cases.json`; the offline scorer never mutates the
database. Evaluation failures should change upstream fields, routing, recall, prompt/context or
model examples before adding production special cases.
