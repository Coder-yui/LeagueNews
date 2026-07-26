import argparse
import asyncio

from app.core.database import SessionLocal
from app.models.normalized_item import NormalizedItem
from app.workflows.translate_item import translate_normalized_item


async def main(item_id: int) -> None:
    with SessionLocal() as db:
        item = db.get(NormalizedItem, item_id)
        if not item:
            raise SystemExit(f"normalized item {item_id} not found")
        translated = await translate_normalized_item(db, item)
        print(
            f"translated normalized_item={translated.id} "
            f"status={translated.translation_status} "
            f"source_language={translated.source_language}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Translate one normalized item")
    parser.add_argument("item_id", type=int)
    args = parser.parse_args()
    asyncio.run(main(args.item_id))
