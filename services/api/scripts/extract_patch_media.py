import argparse
import asyncio

from app.core.database import SessionLocal
from app.models.media_asset import MediaAsset
from app.workflows.understand_media import extract_patch_preview


async def main(media_asset_ids: list[int]) -> None:
    with SessionLocal() as db:
        for media_asset_id in media_asset_ids:
            media_asset = db.get(MediaAsset, media_asset_id)
            if not media_asset:
                raise SystemExit(f"media asset {media_asset_id} not found")
            extraction = await extract_patch_preview(
                db, raw_item=media_asset.raw_item, media_asset=media_asset
            )
            db.commit()
            print(
                f"media_asset={media_asset_id} extraction={extraction.id} "
                f"confidence={extraction.confidence:.3f}"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract Patch Preview tables from media")
    parser.add_argument("media_asset_ids", nargs="+", type=int)
    args = parser.parse_args()
    asyncio.run(main(args.media_asset_ids))
