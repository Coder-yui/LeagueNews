import hashlib
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event import Event, EventRevision
from app.models.intelligence import Digest, DigestRevision


def generate_digest(
    db: Session,
    *,
    digest_type: str,
    cutoff_at,
    timezone: str = "Asia/Shanghai",
) -> Digest:
    if digest_type not in {"daily", "weekly"}:
        raise ValueError("digest_type must be daily or weekly")
    try:
        local_timezone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"invalid IANA timezone: {timezone}") from exc
    if not isinstance(cutoff_at, datetime):
        raise ValueError("cutoff_at must be a datetime")
    local_cutoff = (
        cutoff_at.replace(tzinfo=local_timezone)
        if cutoff_at.tzinfo is None
        else cutoff_at.astimezone(local_timezone)
    )
    local_window_start = local_cutoff - timedelta(
        days=1 if digest_type == "daily" else 7
    )
    normalized_cutoff = local_cutoff.astimezone(UTC)
    window_start = local_window_start.astimezone(UTC)
    rows = db.execute(
        select(Event, EventRevision)
        .join(EventRevision, EventRevision.event_id == Event.id)
        .where(
            Event.status == "active",
            EventRevision.created_at > window_start,
            EventRevision.created_at <= normalized_cutoff,
        )
        .order_by(
            Event.importance_score.desc(),
            Event.last_published_at.desc().nullslast(),
            EventRevision.created_at.desc(),
        )
        .limit(100)
    ).all()
    latest: dict[int, tuple[Event, EventRevision]] = {}
    for event, revision in rows:
        latest.setdefault(event.id, (event, revision))
    snapshot = [
        {
            "event_id": event.id,
            "event_revision": revision.revision,
            "title": revision.title,
            "summary": revision.summary,
            "importance_score": event.importance_score,
        }
        for event, revision in latest.values()
    ]
    input_hash = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    label = "日报" if digest_type == "daily" else "周报"
    title = f"英雄联盟资讯{label} · {local_cutoff.date().isoformat()}"
    body = "\n\n".join(
        f"## {row['title']}\n\n{row['summary']}" for row in snapshot
    ) or "本期暂无已发布事件更新。"
    digest = db.scalar(
        select(Digest).where(
            Digest.digest_type == digest_type,
            Digest.timezone == timezone,
            Digest.cutoff_at == normalized_cutoff,
        )
    )
    if digest is None:
        digest = Digest(
            digest_type=digest_type,
            timezone=timezone,
            window_start=window_start,
            cutoff_at=normalized_cutoff,
            title=title,
            body=body,
            input_hash=input_hash,
            input_snapshot=snapshot,
            generation_metadata={
                "strategy": "event-revision-deterministic-v1",
                "policy_version": "digest-v1",
                "prompt_version": None,
                "model": None,
            },
        )
        db.add(digest)
        db.flush()
        change_note = "initial publication"
    elif digest.input_hash == input_hash:
        return digest
    else:
        digest.current_revision += 1
        digest.title = title
        digest.body = body
        digest.input_hash = input_hash
        digest.input_snapshot = snapshot
        change_note = "late event update or correction"
    db.add(
        DigestRevision(
            digest_id=digest.id,
            revision=digest.current_revision,
            title=title,
            body=body,
            input_hash=input_hash,
            input_snapshot=snapshot,
            change_note=change_note,
        )
    )
    db.commit()
    db.refresh(digest)
    return digest
