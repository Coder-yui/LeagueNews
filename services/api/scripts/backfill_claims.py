import argparse

from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import engine
from app.services.claims import backfill_published_claims


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
        report = backfill_published_claims(
            db,
            limit=args.limit,
            apply=args.apply,
        )
        print(f"Claims to create: {report.claims_created}")
        print(f"EventClaim links to create: {report.event_claims_created}")
        if not args.apply:
            db.rollback()
            print("Dry run only. Re-run with --apply after backup and review.")
            return
        db.commit()
        print(f"Created Claims: {report.claims_created}")
        print(f"Created EventClaim links: {report.event_claims_created}")


if __name__ == "__main__":
    main()
