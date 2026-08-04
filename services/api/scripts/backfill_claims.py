import argparse

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, selectinload

import app.models  # noqa: F401
from app.core.database import engine
from app.models.intelligence import Claim
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.services.claims import extract_traceable_claim


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill one traceable Claim per published item; dry-run by default."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="persist changes; without this flag the command only reports candidates",
    )
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    with Session(engine) as db:
        items = list(
            db.scalars(
                select(NormalizedItem)
                .where(
                    NormalizedItem.publication_status == "published",
                    ~exists().where(
                        Claim.normalized_item_id == NormalizedItem.id
                    ),
                )
                .options(
                    selectinload(NormalizedItem.raw_item).selectinload(
                        RawItem.source
                    ),
                    selectinload(NormalizedItem.claims),
                )
                .order_by(NormalizedItem.id)
                .limit(max(1, min(args.limit, 10_000)))
            )
        )
        print(f"{len(items)} published normalized items need Claim backfill.")
        if not args.apply:
            print("Dry run only. Re-run with --apply after backup and review.")
            return
        for item in items:
            extract_traceable_claim(db, item)
        db.commit()
        print(f"Created Claims for {len(items)} items.")


if __name__ == "__main__":
    main()
