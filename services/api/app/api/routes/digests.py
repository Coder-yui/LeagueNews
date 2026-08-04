from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.models.intelligence import Digest
from app.services.digests import generate_digest

router = APIRouter()


def digest_payload(digest: Digest) -> dict[str, object]:
    return {
        "id": digest.id,
        "digest_type": digest.digest_type,
        "timezone": digest.timezone,
        "window_start": digest.window_start,
        "cutoff_at": digest.cutoff_at,
        "language": digest.language,
        "title": digest.title,
        "body": digest.body,
        "current_revision": digest.current_revision,
        "input_snapshot": digest.input_snapshot,
        "generation_metadata": digest.generation_metadata,
        "published_at": digest.published_at,
        "updated_at": digest.updated_at,
    }


@router.get("")
def list_digests(
    digest_type: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    statement = (
        select(Digest)
        .where(Digest.status == "published")
        .order_by(Digest.cutoff_at.desc())
        .limit(limit)
    )
    if digest_type:
        statement = statement.where(Digest.digest_type == digest_type)
    return [digest_payload(value) for value in db.scalars(statement)]


@router.get("/{digest_id}")
def get_digest(
    digest_id: int, db: Session = Depends(get_db)
) -> dict[str, object]:
    digest = db.scalar(
        select(Digest)
        .where(Digest.id == digest_id, Digest.status == "published")
        .options(selectinload(Digest.revisions))
    )
    if digest is None:
        raise HTTPException(status_code=404, detail="digest not found")
    return {
        **digest_payload(digest),
        "revisions": [
            {
                "revision": value.revision,
                "title": value.title,
                "body": value.body,
                "input_snapshot": value.input_snapshot,
                "change_note": value.change_note,
                "created_at": value.created_at,
            }
            for value in digest.revisions
        ],
    }


@router.post("/generate")
def create_or_revise_digest(
    digest_type: str,
    cutoff_at: datetime,
    timezone: str = "Asia/Shanghai",
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        return digest_payload(
            generate_digest(
                db,
                digest_type=digest_type,
                cutoff_at=cutoff_at,
                timezone=timezone,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
