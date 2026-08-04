from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.content_blocks import text_from_content_blocks
from app.models.intelligence import Claim, EventClaim
from app.models.normalized_item import NormalizedItem


def extract_traceable_claim(db: Session, item: NormalizedItem) -> Claim:
    """Create one lightweight claim without inventing facts or over-splitting."""
    for existing in item.claims:
        if existing.status == "active":
            existing.status = "superseded"
    revision = (
        db.scalar(
            select(func.max(Claim.revision)).where(
                Claim.normalized_item_id == item.id
            )
        )
        or 0
    ) + 1
    raw_text = text_from_content_blocks(item.raw_item.content_blocks)
    entity = item.entities[0] if item.entities else {
        "type": "unknown",
        "display_name": item.translated_title or item.normalized_title,
    }
    evidence = [
        {
            "block_id": block.get("id"),
            "block_index": index,
            "source": "raw",
            "quote": str(block.get("text") or "")[:500],
        }
        for index, block in enumerate(item.raw_item.content_blocks)
        if block.get("text")
    ][:3]
    claim = Claim(
        normalized_item_id=item.id,
        subject=entity,
        predicate="reports",
        object_value={"text": item.summary},
        effective_at=item.raw_item.published_at,
        stance="asserts",
        claim_type="statement",
        evidence=evidence,
        extraction_model=item.analysis_model,
        confidence=1.0 if raw_text else 0.8,
        revision=revision,
        provenance={
            "normalized_item_revision": item.current_revision,
            "raw_item_id": item.raw_item_id,
            "strategy": "single-claim-v1",
        },
    )
    db.add(claim)
    db.flush()
    return claim


def link_item_claims_to_event(
    db: Session,
    *,
    normalized_item_id: int,
    event_id: int,
    relation: str,
) -> None:
    claim_ids = list(
        db.scalars(
            select(Claim.id).where(
                Claim.normalized_item_id == normalized_item_id,
                Claim.status == "active",
            )
        )
    )
    for claim_id in claim_ids:
        if db.get(EventClaim, (event_id, claim_id)) is None:
            db.add(
                EventClaim(event_id=event_id, claim_id=claim_id, relation=relation)
            )
