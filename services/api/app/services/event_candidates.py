"""Load event snapshots; ranking policy belongs to the method assembly."""
from sqlalchemy import select
from app.methods import MethodAssembly
from app.models.event import Event
from app.services.event_semantics import semantic_projection


def load_event_pool(db, *, query=None, include_revision=False):
    statement = select(Event).order_by(Event.id)
    if query is not None:
        from datetime import timedelta
        from sqlalchemy import or_
        statement = statement.where(or_(Event.last_seen_at.is_(None), Event.last_seen_at.between(
            query.published_at - timedelta(days=query.window_days),
            query.published_at + timedelta(days=query.window_days))))
        if query.possible_families:
            statement = statement.where(Event.event_family.in_(query.possible_families))
    return [{"event_id": event.id, **({"revision": event.current_revision} if include_revision else {}), "event_family": event.event_family,
        "products": event.products, "canonical_anchors": event.canonical_anchors,
        "title": event.title, "current_summary": event.current_summary,
        "latest_development": event.latest_development, "key_facts": event.key_facts,
        "lifecycle_status": event.lifecycle_status,
        "last_seen_at": event.last_seen_at.isoformat() if event.last_seen_at else None,
    } for event in db.scalars(statement)]


def recall_event_candidates(
    db,
    *,
    item,
    possible_families,
    entity_hints=None,
    total_limit=None,
    assembly: MethodAssembly,
):
    title, content = semantic_projection(item)
    message = {"title": title, "content": content, "summary": item.summary,
        "products": item.products,
        "published_at": (item.raw_item.published_at or item.raw_item.ingested_at).isoformat()}
    return assembly.recall_events(message=message,
        candidates=load_event_pool(db), possible_families=possible_families,
        entity_hints=entity_hints, total_limit=total_limit)


class RuleEventRetriever:
    """Fetch a bounded time/family pool, then reuse the existing ranking method."""

    def __init__(self, session_factory, assembly):
        self.session_factory = session_factory
        self.assembly = assembly

    async def retrieve(self, query):
        from app.methods.retrieval import RetrievedEvent
        with self.session_factory() as db:
            pool = load_event_pool(db, query=query, include_revision=True)
            revisions = {row["event_id"]: row["revision"] for row in pool}
        ranked = self.assembly.recall_events(
            message=query.message(), candidates=pool,
            possible_families=query.possible_families,
            entity_hints=query.entity_hints, total_limit=query.limit,
        )
        return tuple(RetrievedEvent.model_validate({**row, "revision": revisions[row["event_id"]]})
                     for row in ranked)
