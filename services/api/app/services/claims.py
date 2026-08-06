from dataclasses import dataclass

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session, selectinload

from app.content_blocks import text_from_content_blocks
from app.models.event import EventMessage
from app.models.intelligence import Claim, EventClaim
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem

TIMELINE_PREDICATES = {
    "transfers_to",
    "considered_for",
    "leaves",
    "stays",
    "retires",
    "releases",
    "goes_live",
    "previews",
    "delays",
    "patches",
    "buffs",
    "nerfs",
    "reworks",
    "adds_mode",
    "wins",
    "loses",
    "advances",
    "eliminated",
    "rotates",
    "discounts",
    "gifts",
}


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
        temporal_role="state",
        attribution={
            "claimed_by": item.raw_item.author_name or item.raw_item.source.name,
            "stance": "asserts",
            "certainty": "confirmed",
        },
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


def persist_generated_claims(
    db: Session,
    item: NormalizedItem,
    *,
    fact_claims: list[dict[str, object]],
    attribution: dict[str, object],
) -> list[Claim]:
    for existing in item.claims:
        if existing.status == "active":
            existing.status = "superseded"
    base_revision = (
        db.scalar(
            select(func.max(Claim.revision)).where(
                Claim.normalized_item_id == item.id
            )
        )
        or 0
    )
    evidence = [
        {
            "block_id": block.get("id"),
            "block_index": index,
            "source": "raw",
            "quote": str(block.get("text") or "")[:500],
        }
        for index, block in enumerate(item.raw_item.content_blocks)
        if block.get("text")
    ][:5]
    certainty = str(attribution.get("certainty") or "speculative")
    confidence = {
        "confirmed": 1.0,
        "likely": 0.8,
        "speculative": 0.55,
    }.get(certainty, 0.55)
    persisted = []
    for offset, draft in enumerate(fact_claims, start=1):
        predicate = str(draft.get("predicate") or "")
        if predicate not in TIMELINE_PREDICATES:
            raise ValueError(f"unsupported fact claim predicate: {predicate}")
        claim = Claim(
            normalized_item_id=item.id,
            subject=dict(draft.get("subject") or {}),
            predicate=predicate,
            object_value=dict(draft.get("object") or {}),
            effective_at=item.raw_item.published_at,
            stance=(
                "contradicts"
                if attribution.get("stance") == "refutes"
                else "asserts"
            ),
            claim_type="fact_claim",
            temporal_role=str(draft.get("temporal_role") or "state"),
            attribution={
                **attribution,
                "at": (
                    item.raw_item.published_at.isoformat()
                    if item.raw_item.published_at
                    else None
                ),
            },
            evidence=evidence,
            extraction_model=item.analysis_model,
            schema_version="claim-v2-timeline",
            confidence=confidence,
            revision=base_revision + offset,
            provenance={
                "normalized_item_revision": item.current_revision,
                "raw_item_id": item.raw_item_id,
                "strategy": "atomic-fact-claims-v2",
                "supersedes_hint": draft.get("supersedes_hint"),
            },
        )
        db.add(claim)
        persisted.append(claim)
    db.flush()
    return persisted


def _subject_identity(subject: dict[str, object]) -> tuple[str, str]:
    return (
        str(subject.get("canonical_name") or subject.get("name") or "").casefold(),
        str(subject.get("type") or "").casefold(),
    )


def _resolve_event_supersession(
    db: Session,
    *,
    event_id: int,
    claim: Claim,
) -> None:
    hint = str(claim.provenance.get("supersedes_hint") or "").casefold()
    if not hint and claim.predicate not in {
        "transfers_to",
        "goes_live",
        "releases",
        "patches",
    }:
        return
    statement = (
        select(Claim)
        .join(EventClaim, EventClaim.claim_id == Claim.id)
        .where(
            EventClaim.event_id == event_id,
            Claim.id != claim.id,
        )
        .order_by(Claim.effective_at.desc(), Claim.id.desc())
    )
    if claim.effective_at is not None:
        statement = statement.where(
            (Claim.effective_at.is_(None))
            | (Claim.effective_at <= claim.effective_at)
        )
    prior_claims = list(db.scalars(statement))
    identity = _subject_identity(claim.subject)
    predecessor = next(
        (
            prior
            for prior in prior_claims
            if _subject_identity(prior.subject) == identity
            and (
                not hint
                or hint in str(prior.object_value).casefold()
                or hint in str(prior.subject).casefold()
            )
        ),
        None,
    )
    if predecessor is not None:
        claim.supersedes_claim_id = predecessor.id
        predecessor.status = "superseded"


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
            claim = db.get(Claim, claim_id)
            if claim is not None:
                _resolve_event_supersession(
                    db,
                    event_id=event_id,
                    claim=claim,
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


def supersede_active_claims(
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
        claim.status = "superseded"


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
