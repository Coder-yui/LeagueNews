# Event Aggregation V2 Refactor

## Purpose and first principles

A `NormalizedItem` is an observation. An `Event` is the real-world development to which one or
more observations refer. Event aggregation has one semantic responsibility: for every meaningful
event mention in one message, attach it to a recalled `Event`, create an `Event`, or ignore it.

`event_id` is the durable identity. Products, entities, dates, patch versions, teams, markets and
other anchors are recall and explanation features. They are not a deterministic global identity
system and Python must not use them to prove that two real-world developments are identical.

## Phase 0 audit: current call chain

The pre-refactor chain is:

1. Published `NormalizedItem` enters `aggregate_normalized_item()`.
2. `decide_event_admission()` returns `skip`, `update_existing_only`, or `create_or_update` after
   applying message-type, repost, leak-anchor, promotion and free-champion-rotation policies.
3. `recall_event_candidates()` applies the new product/family routing and then ranks a bounded
   generic candidate set using entities, lightweight text overlap and recency.
4. `_message_payload()` adds regex-derived granularity instructions.
5. `LLMClient.aggregate_events()` makes one logical model request, but its validator performs a
   second semantic decision using identity contracts, family-specific identity projection,
   mythic-shop market/week inference, esports match repair and importance-profile compatibility.
6. The workflow repeats many of those checks, computes a mandatory aggregation key, and either
   creates an event, changes a create into an update when the key already exists, or attaches to a
   candidate.
7. `create_event()` / `add_event_mention()` persist membership and mutate the current event
   projection. The workflow then refreshes importance, credibility, heat and presentation inputs.

## Current responsibility map

| Concern | Current modules | Assessment |
| --- | --- | --- |
| Membership orchestration | `workflows/event_aggregation.py` | Core responsibility obscured by repeated semantic repair |
| Minimal filtering | `domain/event_admission.py` | Contains creation policy and business regex; accidental complexity |
| Candidate retrieval | `services/event_candidates.py` | Retrieval mixed with deterministic identity and hard conflict rules |
| Semantic identity | `domain/event_identity.py`, parts of `domain/event_families.py` and `domain/event_granularity.py` | Accidental deterministic subsystem to delete |
| LLM decision | `services/llm.py`, `schemas/event_aggregation.py` | Membership mixed with projection, identity contracts and business validators |
| Persistence | `services/events.py`, `models/event.py`, `repositories/events.py` | Valuable: transaction, idempotency, audit and many-to-many membership |
| Projections | `services/event_metrics.py`, `services/event_presentation.py`, event importance/credibility/heat domains | Valuable after membership; must not decide membership |

## Sources of accidental complexity to remove

- `event_identity_contract`, identity shapes, `project_event_identity`,
  `identity_is_supported_by_message`, `event_identity_matches`, balance change signatures and
  esports anchor repair.
- Free-champion-rotation natural-language regex and admission states that decide whether creation
  is permitted.
- Mythic-shop market inference, connector-to-market inference and ISO-week identity.
- Daily esports roundup detection and forced per-match identity repair.
- Strong-anchor conflicts, `aggregation_key` matching and family-specific identity projection in
  candidate recall.
- Mandatory canonical identity fields and event-importance semantics in the model result.
- Create-to-update conversion through `aggregation_key`.
- Duplicate semantic validation in schema, LLM validator and apply stage.

## V2 responsibility boundaries and data flow

```text
published NormalizedItem
    -> minimal_event_filter()                 # process / skip only
    -> recall_event_candidates()              # bounded, generic, high recall
    -> bounded message + candidate payload
    -> LLM aggregate_events()                 # one logical call per message
    -> validate_structural_invariants()       # candidate membership and schema only
    -> apply_membership_transaction()
    -> Event + EventMention
    -> refresh_event_projections()
```

The workflow is orchestration. Python validates only deterministic engineering invariants. The
model owns mention extraction, event granularity and the attach/create/ignore decision.

## Membership contract

The V2 result centers on membership:

```json
{
  "mentions": [
    {
      "mention_index": 0,
      "action": "attach",
      "event_id": 123,
      "event_family": "gameplay_balance",
      "relation": "confirms",
      "source_role": "responsible_official",
      "materiality": "material_update",
      "evidence_excerpt": "当前消息中的简短证据",
      "new_event": null,
      "projection": {
        "title": "可选的新展示标题",
        "summary": "可选的新摘要",
        "latest_development": "可选的新进展",
        "key_facts": []
      }
    },
    {
      "mention_index": 1,
      "action": "create",
      "event_id": null,
      "event_family": "cosmetic_release",
      "relation": "reports",
      "source_role": "unknown",
      "materiality": "material_update",
      "evidence_excerpt": "当前消息中的简短证据",
      "new_event": {"title": "事件标题", "summary": "事件摘要"},
      "projection": null
    }
  ]
}
```

`ignore` is represented explicitly when the model needs to account for a fragment, but it produces
no membership row. `canonical_anchors` may remain as optional descriptive metadata on creation;
they are never required and never checked for semantic identity. `event_id` is the only persisted
Event identity.

## Candidate recall

Recall is bounded to a recent generic window and uses:

- product hard gating and routed family compatibility;
- entity overlap;
- lightweight title/summary token overlap;
- recent activity.

Candidates are capped globally (default 12). The payload includes `event_id`, current description,
family, products, descriptive anchors and recent activity. It does not include an identity key or a
hard conflict verdict.

## Deterministic validation boundary

Python validates:

- schema and controlled enum values;
- unique, contiguous mention indexes;
- create has no `event_id` and contains a minimal new-event description;
- attach references an existing event from this request's candidate set;
- create/attach family is inside the `products + topics` derived event space;
- non-ignore mentions contain evidence;
- per-item-revision run and mention idempotency;
- one default logical model call, retry audit, transaction atomicity and rollback;
- projection refresh is invoked after membership is persisted.

Python does not decide whether two events are semantically the same, whether a message supports a
family-specific identity, whether a shop belongs to a market/week, or whether a match/patch/change
shape is complete.

## Membership and projection

Membership persistence stores `EventMention` evidence, relation, source role, materiality, source
reliability and source time. Optional model projection proposals can update the event's descriptive
fields only for material attaches. Importance is derived after membership from the existing
`NormalizedItem` importance calculation; credibility, heat, references and presentation are also
refreshed after the membership transaction has been assembled. None of these values can reject or
redirect membership.

## Test versus evaluation boundary

Pytest covers deterministic engineering behavior:

- process/skip, bounded recall, zero mentions and one logical model call;
- attach, create, multiple mentions and mixed attach/create;
- unknown candidate rejection, invalid schema, idempotency, retry and run audit;
- atomic rollback, mention persistence and projection refresh.

The following business cases move to evaluation fixtures: weekly free champions, mythic shop and
CN/GLOBAL cycles, esports roundups/matches/scores/schedules, gameplay balance and patch lifecycle,
activities/rewards/cosmetics, roster moves, leaks/confirmation/denial, reposts and multi-event
announcements. Evaluation labels measure false merge, false split, missed/wrong attach,
unnecessary/missed create and multi-mention completeness. Failures should first change the prompt,
examples, recall or supplied context—not add production regex or family-specific Python branches.

## Planned deletion and rewrite list

- Delete `domain/event_identity.py` and `domain/event_granularity.py` after callers are removed.
- Reduce `domain/event_families.py` to topic hints and generic entity extraction.
- Replace three-state `domain/event_admission.py` with a process/skip minimal filter.
- Rewrite `schemas/event_aggregation.py`, the aggregation portion of `services/llm.py`,
  `services/event_candidates.py` and `workflows/event_aggregation.py`.
- Keep the event model, repository, revisions, run audit, mention constraints and projection
  services; simplify `services/events.py` so importance snapshots are optional workflow inputs.
- Replace special-case workflow and LLM tests with engineering-invariant tests. Preserve event API,
  persistence and metrics tests where they assert stable public or deterministic behavior.
- Add versioned evaluation fixtures and an offline evaluator that never mutates production data.

## Complexity comparison

The comparison is based on the current branch diff and the pre-refactor files preserved in git
history. Production membership logic is now concentrated in the workflow, generic candidate
recall, the small routing module and the persistence service; the deleted identity/granularity
modules are no longer compatibility facades.

| Measure | Before V2 | After V2 | Change |
| --- | ---: | ---: | ---: |
| Deleted event identity/granularity helpers | 628 LOC | 0 | -628 |
| Candidate/admission/workflow production LOC | 1,437 | 815 | -622 |
| Workflow conditional branches (`if`/`elif`) | 52 | 46 | -6 |
| Identity/granularity regex declarations | 11 | 0 | -11 |
| Aggregation schema fields (all V2 models) | 33 | 24 | -9 |
| Event workflow test LOC | 1,721 | 611 | -1,110 |

Counts were measured against the target branch's pre-change files with `git show HEAD:<path>` and
the current working tree on 2026-08-13. The candidate/admission/workflow total excludes the
deleted identity/granularity files; those are reported separately so the reduction is not hidden.

The remaining rules are structural safety checks, generic routing, or projections. Business cases
are represented in the evaluation fixture rather than production identity code.

## Non-goals

Collection, OCR, translation, message classification, message importance, RawItem ingestion and
immutable source evidence are unchanged. No old/new dual path or compatibility facade will remain.
