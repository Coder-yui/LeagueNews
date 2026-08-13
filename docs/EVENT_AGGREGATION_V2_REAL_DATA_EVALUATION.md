# Event Aggregation V2 Real-data Evaluation

## Scope

This baseline was prepared on 2026-08-13 from the local development database
`localhost/lol_daily_intel`. It sampled current published `NormalizedItem` rows and did not modify
RawItems, NormalizedItems or source evidence. The event layer had already been intentionally reset,
so labels use stable logical `event_key` values rather than database Event IDs.

The full fixture is a human-reviewed offline baseline, not an invented model result. A selected
five-case subset was additionally run against the configured event model without persisting Events;
the raw structured decisions and score are saved separately so the two kinds of evidence remain
distinguishable.

## Coverage and results

The fixture contains 13 semantic cases and 26 real messages:

| Expected behavior | Representative data | Evaluation concern |
| --- | --- | --- |
| create | BLG vs JDG fixture | missed create, wrong attach |
| attach | Nasus PBE changes; WE vs BLG reports; July 31 hotfix | false split, missed attach |
| ignore | weekly free champions; unsupported TES/WBG swap speculation | unnecessary create, wrong attach |
| multi-mention | daily results roundup; TFT character + activity; PBE balance + bugfix | missed event, false merge |
| recurring series | CN/GLOBAL daily mythic-shop rotations | false merge, wrong attach |
| correction | Bin-unreachable rumor correction | missed attach, wrong relation |
| repost | Alistar icon original and repost | false split, false official confirmation |

Human label totals:

- 26 sampled messages;
- 27 expected Event membership decisions;
- 2 expected zero-membership messages;
- 11 expected attaches, 16 expected creates;
- representative risk labels cover false merge, false split, wrong/missed attach, unnecessary
  create, missed create and multi-mention completeness.

The fixture itself validates successfully with:

```bash
services/api/.venv/bin/python services/api/scripts/evaluate_event_aggregation.py
```

To obtain actual model metrics, export predictions in the scorer's documented item/mention shape
and pass the file to that command. Evaluation failures should first change prompt examples,
candidate recall or supplied context. They must not directly produce family-specific Python
identity rules.

## Live model sample

The non-persisting live runner evaluated 5 cases / 7 messages covering ignore, create, attach,
repost and multi-mention behavior. After the evaluator was corrected to score `event_family` (and
not merely create order), the initial run was 3/7 exact. It exposed three prompt-boundary errors:

- weekly free champions were unnecessarily created;
- a code-redemption activity was classified as `cosmetic_release` rather than `player_activity`;
- a concrete upcoming match was classified as `esports_schedule` rather than `esports_match`.

The fix was limited to generic prompt family definitions and the explicit editorial policy for
weekly free-character rotations. No Python family branch, regex, identity helper or recall special
case was added. Re-running the same sample produced 7/7 exact item-level membership/family results,
with no missing or extra membership. This small calibrated sample is evidence that the intended
prompt/evaluation feedback loop works; it is not a claim of 100% accuracy on the full 26-message
fixture or unseen data.

Artifacts:

- `services/api/scripts/evaluate_event_aggregation_live.py`
- `services/api/evals/event_aggregation_v2_live_2026-08-13.json`

## Important ambiguities

- Item 1783's textual summary names two completed matches while its image may also describe future
  fixtures. The baseline requires the two supported attachments and flags possible missed mentions
  for richer media-aware runs.
- Items 1809-1814 explicitly say daily rotation. The baseline therefore separates day and market,
  but that conclusion lives only in evaluation, not connector/ISO-week code.
- Item 1755 labels the dated activity separately from the character release because each can have an
  independent follow-up lifecycle.
- Item 1706 labels the balance batch and bugfix batch separately for the same reason. Prompt or
  editorial policy can revise these labels without a production rule change.
