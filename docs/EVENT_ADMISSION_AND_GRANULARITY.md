# Event Filtering, Recall, and Granularity

> Policy version: `event-aggregation-v6-lifecycle-cohesion`

## Minimal filter

The filter returns only `process` or `skip`. It skips an unpublished item or an item with no usable
semantic text. It does not decide whether an item may create an Event and contains no business
regex for free champions, shops, esports, patches, reposts, leaks or promotions.

## Candidate recall

Recall is a bounded high-recall operation. It looks at recent Events and ranks them with generic
signals: topic-to-family hints, product overlap, entity overlap, lightweight title/summary lexical
overlap and recent activity. Product or anchor differences are not hard conflicts. Candidate
metadata is context for the model and never proof of identity.

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

Family-specific examples are evaluation data, not Python policy. See
`services/api/evals/event_aggregation_v2_cases.json`.
