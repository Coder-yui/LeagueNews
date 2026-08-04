from dataclasses import dataclass

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session, selectinload

from app.content_blocks import text_from_content_blocks
from app.models.event import EventMessage
from app.models.intelligence import Claim, EventClaim
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem


@dataclass(frozen=True, slots=True)
class ClaimBackfillReport:
    claims_created: int
    event_claims_created: int


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


def unlink_item_claims_from_event(
    db: Session,
    *,
    normalized_item_id: int,
    event_id: int,
) -> None:
    claim_ids = select(Claim.id).where(
        Claim.normalized_item_id == normalized_item_id
    )
    db.execute(
        delete(EventClaim).where(
            EventClaim.event_id == event_id,
            EventClaim.claim_id.in_(claim_ids),
        )
    )


def withdraw_active_claims(
    db: Session,
    *,
    normalized_item_id: int,
) -> None:
    for claim in db.scalars(
        select(Claim).where(
            Claim.normalized_item_id == normalized_item_id,
            Claim.status == "active",
        )
    ):
        claim.status = "withdrawn"


def backfill_published_claims(
    db: Session,
    *,
    limit: int = 500,
    apply: bool = False,
) -> ClaimBackfillReport:
    """Create missing active Claims and membership links for published items."""
    active_claim_exists = exists(
        select(Claim.id).where(
            Claim.normalized_item_id == NormalizedItem.id,
            Claim.status == "active",
        )
    )
    active_membership_has_unlinked_claim = exists(
        select(EventMessage.event_id).where(
            EventMessage.normalized_item_id == NormalizedItem.id,
            EventMessage.membership_status == "active",
            exists(
                select(Claim.id).where(
                    Claim.normalized_item_id == NormalizedItem.id,
                    Claim.status == "active",
                    ~exists(
                        select(EventClaim.event_id).where(
                            EventClaim.event_id == EventMessage.event_id,
                            EventClaim.claim_id == Claim.id,
                        )
                    ),
                )
            ),
        )
    )
    items = list(
        db.scalars(
            select(NormalizedItem)
            .where(
                NormalizedItem.publication_status == "published",
                (
                    ~active_claim_exists
                    | active_membership_has_unlinked_claim
                ),
            )
            .options(
                selectinload(NormalizedItem.raw_item).selectinload(RawItem.source),
                selectinload(NormalizedItem.claims),
            )
            .order_by(NormalizedItem.id)
            .limit(max(1, min(limit, 10_000)))
        )
    )
    claims_created = 0
    event_claims_created = 0
    for item in items:
        active_claims = [claim for claim in item.claims if claim.status == "active"]
        active_memberships = list(
            db.scalars(
                select(EventMessage).where(
                    EventMessage.normalized_item_id == item.id,
                    EventMessage.membership_status == "active",
                )
            )
        )
        if not active_claims:
            claims_created += 1
            if apply:
                active_claims = [extract_traceable_claim(db, item)]
        for membership in active_memberships:
            for claim in active_claims or [None]:
                if claim is None:
                    event_claims_created += 1
                    continue
                if db.get(EventClaim, (membership.event_id, claim.id)) is None:
                    event_claims_created += 1
                    if apply:
                        db.add(
                            EventClaim(
                                event_id=membership.event_id,
                                claim_id=claim.id,
                                relation=membership.evidence_stance,
                            )
                        )
        if apply:
            db.flush()
    return ClaimBackfillReport(
        claims_created=claims_created,
        event_claims_created=event_claims_created,
    )
