"""Load event snapshots; ranking policy belongs to the method assembly."""
from sqlalchemy import select
from app.models.event import Event
from app.services.event_semantics import semantic_projection


def load_event_pool(db):
    return [{"event_id": event.id, "event_family": event.event_family,
        "products": event.products, "canonical_anchors": event.canonical_anchors,
        "title": event.title, "current_summary": event.current_summary,
        "latest_development": event.latest_development, "key_facts": event.key_facts,
        "lifecycle_status": event.lifecycle_status,
        "last_seen_at": event.last_seen_at.isoformat() if event.last_seen_at else None,
    } for event in db.scalars(select(Event).order_by(Event.id))]


def recall_event_candidates(db, *, item, possible_families, entity_hints=None, total_limit=None, assembly=None):
    from app.methods import MethodAssembly
    title, content = semantic_projection(item)
    message = {"title": title, "content": content, "summary": item.summary,
        "products": item.products,
        "published_at": (item.raw_item.published_at or item.raw_item.ingested_at).isoformat()}
    return (assembly or MethodAssembly()).recall_events(message=message,
        candidates=load_event_pool(db), possible_families=possible_families,
        entity_hints=entity_hints, total_limit=total_limit)
