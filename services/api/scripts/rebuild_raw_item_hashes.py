"""Upgrade persisted RawItem hashes to the current source-semantic algorithm."""

from sqlalchemy import select, update

from app.content_blocks import content_hash
from app.core.database import SessionLocal
from app.models.raw_item import RawItem


def main() -> None:
    with SessionLocal() as db:
        items = list(db.scalars(select(RawItem)))
        updated_count = 0
        for item in items:
            rebuilt_hash = content_hash(item.content_blocks)
            if item.content_hash_version == 2 and item.content_hash == rebuilt_hash:
                continue
            db.execute(
                update(RawItem)
                .where(RawItem.id == item.id)
                .values(
                    content_hash=rebuilt_hash,
                    content_hash_version=2,
                )
            )
            updated_count += 1
        db.commit()
    print(f"Upgraded {updated_count} RawItem content hashes to version 2.")


if __name__ == "__main__":
    main()
