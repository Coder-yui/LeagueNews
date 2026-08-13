from dataclasses import dataclass
from typing import Literal

from app.domain.event_families import (
    EventSpace,
    anchors_from_entities,
    possible_event_families,
)
from app.models.normalized_item import NormalizedItem


AdmissionKind = Literal["process", "skip"]


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    decision: AdmissionKind
    event_space: EventSpace
    reasons: tuple[str, ...]
    entity_hints: dict[str, object]

def derive_event_space(item: NormalizedItem) -> EventSpace:
    """Route an already-classified message into a bounded Event taxonomy space."""

    products = tuple(str(product) for product in item.products)
    return EventSpace(
        products=products,
        possible_families=possible_event_families(products, item.topics),
    )


def _has_semantic_text(item: NormalizedItem) -> bool:
    return any(
        isinstance(value, str) and bool(value.strip())
        for value in (
            item.normalized_title,
            item.normalized_text,
            item.summary,
            item.translated_title,
            item.translated_text,
        )
    )


def minimal_event_filter(item: NormalizedItem) -> AdmissionDecision:
    """Skip only inputs that plainly cannot participate in event membership."""

    event_space = derive_event_space(item)
    entities = anchors_from_entities(item.entities)
    if item.publication_status != "published":
        return AdmissionDecision(
            "skip", event_space, ("normalized item is not published",), entities
        )
    if not _has_semantic_text(item):
        return AdmissionDecision(
            "skip", event_space, ("normalized item has no semantic text",), entities
        )
    return AdmissionDecision("process", event_space, ("semantic published message",), entities)
