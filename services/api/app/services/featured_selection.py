"""Read complete daily candidate sets, then apply the configured selection method."""

from collections import defaultdict
from datetime import UTC
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from app.models.normalized_item import NormalizedItem
from app.methods import FeaturedCandidate, MethodAssembly
from app.services.raw_item_versions import latest_normalized_item_condition


def selected_featured_ids(db: Session, *, assembly: MethodAssembly | None = None) -> set[int]:
    assembly = assembly or MethodAssembly()
    items = db.scalars(
        select(NormalizedItem)
        .options(selectinload(NormalizedItem.raw_item))
        .where(
            latest_normalized_item_condition(),
            NormalizedItem.publication_status == "published",
        )
    )
    days = defaultdict(list)
    timezone = ZoneInfo("Asia/Shanghai")
    for item in items:
        timestamp = item.raw_item.published_at or item.raw_item.ingested_at
        timestamp = timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp
        days[timestamp.astimezone(timezone).date()].append(
            FeaturedCandidate(
                normalized_item_id=item.id,
                importance_score=item.importance_score,
                content_form=item.content_form,
            )
        )
    return {
        item_id
        for candidates in days.values()
        for item_id in assembly.select_featured(candidates).selected_item_ids
    }
