"""Probe one candidate from every active automated source without downstream jobs."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

import app.models  # noqa: F401
from app.connectors.base import ConnectorRequest, ConnectorSource
from app.connectors.config import validate_connector_config, validate_external_key
from app.connectors.registry import connector_registry
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.source import Source
from app.services.ingestion import ingest_connector_items
from scripts.bulk_collect_raw_items import _in_time_range, _parse_datetime_argument


MANUAL_CONNECTOR = "manual"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _path(value: Path) -> Path:
    return value if value.is_absolute() else settings.project_root / value


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


def _new_source_state(source: Source) -> dict[str, Any]:
    return {
        "source_id": source.id,
        "name": source.name,
        "connector_type": source.connector_type,
        "status": "pending",
        "discovered": 0,
        "eligible": 0,
        "created": 0,
        "revised": 0,
        "skipped": 0,
        "error_type": "",
    }


def _report_markdown(state: dict[str, Any]) -> str:
    source_states = list(state["sources"].values())
    totals = {
        key: sum(int(source.get(key, 0) or 0) for source in source_states)
        for key in ("discovered", "eligible", "created", "revised", "skipped")
    }
    successful = sum(source.get("status") == "success" for source in source_states)
    failed = sum(source.get("status") == "failed" for source in source_states)
    lines = [
        "# 信源单条试采报告",
        "",
        f"- 状态：`{state['status']}`",
        f"- 时间窗：`{state['since']}` 至 `{state['until']}`（含边界）",
        f"- 相邻信源间隔：`{state['source_delay_seconds']} 秒`",
        "- 下游处理：未启用；所有入库调用 `enqueue_downstream=False`",
        "",
        "| Source ID | 信源 | Connector | 结果 | 候选 | 窗内候选 | 新建 | 修订 | 去重 | 错误类型 |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for source in sorted(source_states, key=lambda item: int(item["source_id"])):
        lines.append(
            "| {source_id} | {name} | {connector_type} | {status} | {discovered} | "
            "{eligible} | {created} | {revised} | {skipped} | {error_type} |".format(
                **source
            )
        )
    lines.extend(
        [
            "",
            "## 汇总",
            "",
            f"- 成功：`{successful}` / `{len(source_states)}`",
            f"- 失败：`{failed}`",
            f"- 发现候选：`{totals['discovered']}`",
            f"- 窗内候选：`{totals['eligible']}`",
            f"- 新建 RawItem：`{totals['created']}`",
            f"- 修订 RawItem：`{totals['revised']}`",
            f"- 去重跳过：`{totals['skipped']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _save_state(state: dict[str, Any], state_file: Path, report_file: Path) -> None:
    state["updated_at"] = _now()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_file.with_name(f".{state_file.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, state_file)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(_report_markdown(state), encoding="utf-8")


def _ensure_local_database() -> None:
    hostname = urlparse(settings.database_url).hostname
    if hostname not in {None, "localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("refusing collection because the configured database is not local")


async def _run(args: argparse.Namespace) -> None:
    since = _parse_datetime_argument(args.since, option="--since")
    until = _parse_datetime_argument(args.until, option="--until")
    if until < since:
        raise ValueError("--until must be greater than or equal to --since")
    _ensure_local_database()
    selected_ids = set(args.source_id) if args.source_id else None
    connector_types = set(args.connector_type or []) or None
    with SessionLocal() as db:
        query = select(Source).where(
            Source.is_active.is_(True), Source.connector_type != MANUAL_CONNECTOR
        )
        if selected_ids:
            query = query.where(Source.id.in_(selected_ids))
        if connector_types:
            query = query.where(Source.connector_type.in_(connector_types))
        sources = list(db.scalars(query.order_by(Source.id)))
    if selected_ids and {source.id for source in sources} != selected_ids:
        found_ids = {source.id for source in sources}
        raise RuntimeError(
            f"requested source IDs are unavailable or excluded: {sorted(selected_ids - found_ids)}"
        )
    if not sources:
        raise RuntimeError("no active automated sources found")

    state_file = _path(args.state_file)
    report_file = _path(args.report_file)
    state = {
        "status": "running",
        "since": since.isoformat(),
        "until": until.isoformat(),
        "source_delay_seconds": args.source_delay,
        "started_at": _now(),
        "updated_at": _now(),
        "finished_at": None,
        "sources": {str(source.id): _new_source_state(source) for source in sources},
    }
    _save_state(state, state_file, report_file)

    for index, source in enumerate(sources):
        source_state = state["sources"][str(source.id)]
        try:
            connector = connector_registry.create(source.connector_type)
            batch = await connector.collect(
                ConnectorRequest(
                    source=_source_context(source),
                    limit=1,
                    since=since,
                    options={},
                )
            )
            eligible = [
                item
                for item in batch
                if _in_time_range(item.published_at, since=since, until=until)
            ]
            with SessionLocal() as db:
                live_source = db.get(Source, source.id)
                if live_source is None or not live_source.is_active:
                    raise RuntimeError("source is missing or inactive")
                result = await ingest_connector_items(
                    db,
                    source=live_source,
                    items=eligible,
                    enqueue_downstream=False,
                )
            source_state.update(
                status="success",
                discovered=len(batch),
                eligible=len(eligible),
                created=len(result.created),
                revised=len(result.revised),
                skipped=len(result.skipped),
            )
        except Exception as exc:
            source_state.update(status="failed", error_type=type(exc).__name__)
        _save_state(state, state_file, report_file)
        if index < len(sources) - 1:
            await asyncio.sleep(args.source_delay)

    state["status"] = "completed"
    state["finished_at"] = _now()
    _save_state(state, state_file, report_file)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe one candidate from every active automated source."
    )
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--source-delay", type=float, default=30.0)
    parser.add_argument("--source-id", type=int, action="append")
    parser.add_argument("--connector-type", action="append")
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(".run/collection_preflight_state.json"),
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        default=Path(".run/collection_preflight_report.md"),
    )
    args = parser.parse_args()
    args.source_delay = max(args.source_delay, 0.0)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
