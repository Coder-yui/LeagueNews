"""Probe each CN source with a single-item fetch to verify connectivity."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import app.models  # noqa: F401
from app.connectors.base import ConnectorRequest, ConnectorSource
from app.connectors.config import validate_connector_config, validate_external_key
from app.connectors.registry import connector_registry
from app.core.database import SessionLocal
from app.models.source import Source
from sqlalchemy import select

CN_CONNECTOR_TYPES = {"tencent_lol", "weibo", "baidu_tieba"}


def _source_context(source: Source) -> ConnectorSource:
    return ConnectorSource(
        id=source.id,
        name=source.name,
        connector_type=source.connector_type,
        external_key=validate_external_key(source.connector_type, source.external_key),
        base_url=source.base_url,
        connector_config=validate_connector_config(
            source.connector_type, source.connector_config
        ),
    )


async def probe_source(source: Source, timeout: float = 180.0) -> dict:
    since = datetime(2026, 8, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    request = ConnectorRequest(
        source=_source_context(source),
        limit=1,
        since=since,
        options={},
        historical=False,
    )
    connector = connector_registry.create(source.connector_type)
    try:
        started = datetime.now(UTC)
        batch = await asyncio.wait_for(connector.collect(request), timeout=timeout)
        elapsed = (datetime.now(UTC) - started).total_seconds()
        if batch:
            item = batch[0]
            return {
                "ok": True,
                "elapsed_seconds": round(elapsed, 2),
                "batch_size": len(batch),
                "truncated": batch.truncated,
                "first_external_id": item.external_id,
                "first_published_at": item.published_at.isoformat() if item.published_at else None,
                "first_title": (item.native_title or "")[:80],
            }
        return {
            "ok": True,
            "elapsed_seconds": round(elapsed, 2),
            "batch_size": 0,
            "truncated": batch.truncated,
            "note": "empty batch returned",
        }
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"timeout after {timeout}s"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}


async def main() -> int:
    with SessionLocal() as db:
        sources = list(
            db.scalars(
                select(Source)
                .where(Source.is_active.is_(True))
                .where(Source.connector_type.in_(CN_CONNECTOR_TYPES))
                .order_by(Source.connector_type, Source.id)
            )
        )
    if not sources:
        print("ERROR: no active CN sources found in database", file=sys.stderr)
        return 1

    print(f"Probing {len(sources)} CN sources (1 item each, no ingestion)...\n")
    results: list[tuple[Source, dict]] = []
    for idx, source in enumerate(sources, 1):
        print(f"[{idx}/{len(sources)}] #{source.id} {source.connector_type:13s} {source.name} ...", end=" ", flush=True)
        result = await probe_source(source)
        results.append((source, result))
        if result["ok"]:
            print(
                f"OK ({result['elapsed_seconds']}s, size={result['batch_size']}) "
                f"{result.get('first_published_at') or '-'} "
                f"{result.get('first_title') or result.get('note', '')}"
            )
        else:
            print(f"FAIL: {result['error']}")
        if idx < len(sources):
            await asyncio.sleep(3.0)

    ok_count = sum(1 for _, r in results if r["ok"])
    fail_count = len(results) - ok_count
    print(f"\n=== Summary: {ok_count} ok, {fail_count} failed ===")
    if fail_count:
        print("Failed sources:")
        for source, result in results:
            if not result["ok"]:
                print(f"  #{source.id} {source.connector_type} {source.name}: {result['error']}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
