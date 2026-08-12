"""Collect historical connector candidates into RawItems without downstream jobs.

This script is intentionally separate from the scheduled connector runner.  It
keeps a local, atomic state file so a process or network interruption can be
resumed without losing the per-source cursor.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

import app.models  # noqa: F401
from app.connectors.base import ConnectorRequest, ConnectorSource
from app.connectors.config import validate_connector_config, validate_external_key
from app.connectors.registry import connector_registry
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.source import Source
from app.services.ingestion import ingest_connector_items


DEFAULT_SINCE = "2026-08-01T00:00:00+08:00"
DEFAULT_STATE_FILE = "bulk_collect_raw_items_20260801_state.json"
DEFAULT_REPORT_FILE = "bulk_collect_raw_items_20260801_report.md"
MANUAL_CONNECTOR = "manual"
X_CONNECTOR = "x_twitter"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _parse_since(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid --since datetime: {value!r}") from exc
    if parsed.utcoffset() is None:
        raise ValueError("--since must include a timezone offset")
    return parsed


def _safe_text(value: object, limit: int = 2000) -> str:
    return str(value).replace("\x00", "")[:limit]


def _source_state(source: Source) -> dict[str, Any]:
    return {
        "source_id": source.id,
        "name": source.name,
        "connector_type": source.connector_type,
        "status": "pending",
        "cursor": {},
        "batches": 0,
        "discovered": 0,
        "created": 0,
        "revised": 0,
        "skipped": 0,
        "retry_count": 0,
        "errors": [],
        "last_error": None,
        "started_at": None,
        "finished_at": None,
        "updated_at": _now(),
    }


def _state_path(path: Path) -> Path:
    return path if path.is_absolute() else settings.project_root / path


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _report_markdown(state: dict[str, Any]) -> str:
    source_states = list(state.get("sources", {}).values())
    totals = {
        key: sum(int(source.get(key, 0) or 0) for source in source_states)
        for key in ("batches", "discovered", "created", "revised", "skipped", "retry_count")
    }
    statuses: dict[str, int] = {}
    for source in source_states:
        status = str(source.get("status", "unknown"))
        statuses[status] = statuses.get(status, 0) + 1
    status_text = str(state.get("status", "unknown"))
    failed = [source for source in source_states if source.get("status") == "failed"]
    lines = [
        "# RawItem 批量采集报告",
        "",
        f"- 状态：`{status_text}`",
        f"- 采集起点：`{state.get('since', '')}`",
        f"- 任务开始：`{state.get('started_at', '')}`",
        f"- 最近更新：`{state.get('updated_at', '')}`",
        f"- 任务结束：`{state.get('finished_at') or '尚未结束'}`",
        f"- 本次包含信源类型：`{', '.join(state.get('included_connector_types', [])) or '全部非手动类型'}`",
        f"- 排除信源类型：`{', '.join(state.get('excluded_connector_types', [])) or '无'}`",
        "- 下游处理：未启用；本任务所有入库均使用 `enqueue_downstream=False`，不会创建 pipeline job",
        "",
        "## 信源明细",
        "",
        "| Source ID | 信源 | Connector | 状态 | 批次 | 发现 | 新建 | 修订 | 跳过 | 重试 | 最近错误 |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for source in sorted(source_states, key=lambda item: int(item.get("source_id", 0))):
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(source.get(key, ""))
                for key in (
                    "source_id",
                    "name",
                    "connector_type",
                    "status",
                    "batches",
                    "discovered",
                    "created",
                    "revised",
                    "skipped",
                    "retry_count",
                    "last_error",
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 汇总",
            "",
            f"- 信源状态计数：`{json.dumps(statuses, ensure_ascii=False, sort_keys=True)}`",
            f"- 批次：`{totals['batches']}`",
            f"- 发现候选：`{totals['discovered']}`",
            f"- 新建 RawItem：`{totals['created']}`",
            f"- 修订 RawItem：`{totals['revised']}`",
            f"- 去重跳过：`{totals['skipped']}`",
            f"- 网络重试：`{totals['retry_count']}`",
            f"- 失败信源：`{len(failed)}`",
            "",
            "游标和中间统计保存在同目录的 JSON 状态文件中。重新运行同一命令会跳过已完成信源，并从未完成信源的最近游标继续。",
        ]
    )
    return "\n".join(lines) + "\n"


def _save_state(state: dict[str, Any], state_file: Path, report_file: Path) -> None:
    state["updated_at"] = _now()
    _write_json_atomic(state_file, state)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_file.with_name(f".{report_file.name}.{os.getpid()}.tmp")
    temporary.write_text(_report_markdown(state), encoding="utf-8")
    os.replace(temporary, report_file)


def _load_state(
    state_file: Path,
    *,
    since: str,
    sources: list[Source],
    limit: int,
    included_connector_types: list[str],
    excluded_connector_types: list[str],
) -> dict[str, Any]:
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise RuntimeError(f"state file root is not an object: {state_file}")
        if state.get("since") != since:
            raise RuntimeError(
                f"state file since={state.get('since')!r} does not match --since={since!r}; "
                "choose another --state-file"
            )
        if not isinstance(state.get("sources"), dict):
            raise RuntimeError("state file has no valid sources map")
        for source in sources:
            key = str(source.id)
            if key not in state["sources"]:
                state["sources"][key] = _source_state(source)
            else:
                saved = state["sources"][key]
                saved["name"] = source.name
                saved["connector_type"] = source.connector_type
                if saved.get("status") == "running":
                    saved["status"] = "pending"
        state["limit"] = limit
        state["included_connector_types"] = included_connector_types
        state["excluded_connector_types"] = excluded_connector_types
        return state

    state = {
        "version": 1,
        "status": "running",
        "since": since,
        "limit": limit,
        "included_connector_types": included_connector_types,
        "excluded_connector_types": excluded_connector_types,
        "started_at": _now(),
        "updated_at": _now(),
        "finished_at": None,
        "sources": {str(source.id): _source_state(source) for source in sources},
    }
    return state


def _active_sources(
    source_ids: set[int] | None,
    *,
    connector_types: set[str] | None,
    include_x: bool,
) -> list[Source]:
    with SessionLocal() as db:
        excluded = {MANUAL_CONNECTOR}
        if not include_x:
            excluded.add(X_CONNECTOR)
        query = select(Source).where(Source.is_active.is_(True))
        query = query.where(Source.connector_type.not_in(excluded))
        if connector_types:
            query = query.where(Source.connector_type.in_(connector_types))
        if source_ids:
            query = query.where(Source.id.in_(source_ids))
        return list(db.scalars(query.order_by(Source.id)))


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


async def _collect_source(
    source_id: int,
    source_state: dict[str, Any],
    *,
    since: datetime,
    limit: int,
    batch_delay: float,
    error_delay: float,
    max_retries: int,
    state: dict[str, Any],
    state_file: Path,
    report_file: Path,
) -> None:
    source_state["status"] = "running"
    source_state["started_at"] = source_state.get("started_at") or _now()
    _save_state(state, state_file, report_file)
    connector = None

    while True:
        cursor = dict(source_state.get("cursor") or {})
        if connector is None:
            with SessionLocal() as db:
                source = db.get(Source, source_id)
                if source is None or not source.is_active:
                    raise RuntimeError(f"source {source_id} is missing or inactive")
                connector = connector_registry.create(source.connector_type)

        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                with SessionLocal() as db:
                    source = db.get(Source, source_id)
                    if source is None or not source.is_active:
                        raise RuntimeError(f"source {source_id} is missing or inactive")
                    request = ConnectorRequest(
                        source=_source_context(source),
                        limit=limit,
                        since=since,
                        options={},
                        cursor=cursor,
                    )
                    batch = await connector.collect(request)
                    result = await ingest_connector_items(
                        db,
                        source=source,
                        items=list(batch),
                        enqueue_downstream=False,
                    )
                source_state["batches"] = int(source_state.get("batches", 0)) + 1
                source_state["discovered"] = int(source_state.get("discovered", 0)) + len(batch)
                source_state["created"] = int(source_state.get("created", 0)) + len(result.created)
                source_state["revised"] = int(source_state.get("revised", 0)) + len(result.revised)
                source_state["skipped"] = int(source_state.get("skipped", 0)) + len(result.skipped)
                next_cursor = dict(batch.next_cursor or {})
                if batch.truncated and not batch:
                    raise RuntimeError(
                        "connector returned an empty truncated batch; refusing to loop without progress"
                    )
                if batch.truncated and next_cursor == cursor:
                    raise RuntimeError(
                        "connector cursor did not advance for a truncated batch"
                    )
                source_state["cursor"] = next_cursor
                source_state["last_error"] = None
                _save_state(state, state_file, report_file)
                print(
                    f"source={source_id} batch={source_state['batches']} "
                    f"discovered={len(batch)} created={len(result.created)} "
                    f"revised={len(result.revised)} skipped={len(result.skipped)} "
                    f"truncated={batch.truncated}",
                    flush=True,
                )
                if not batch.truncated:
                    source_state["status"] = "completed"
                    source_state["finished_at"] = _now()
                    _save_state(state, state_file, report_file)
                    return
                await asyncio.sleep(batch_delay)
                break
            except Exception as exc:
                last_error = exc
                source_state["retry_count"] = int(source_state.get("retry_count", 0)) + 1
                source_state["last_error"] = _safe_text(exc)
                errors = list(source_state.get("errors") or [])
                errors.append({"at": _now(), "attempt": attempt, "error": _safe_text(exc)})
                source_state["errors"] = errors[-5:]
                _save_state(state, state_file, report_file)
                if attempt < max_retries:
                    wait_seconds = error_delay * attempt
                    print(
                        f"source={source_id} attempt={attempt} failed; retrying in "
                        f"{wait_seconds:g}s: {_safe_text(exc)}",
                        flush=True,
                    )
                    await asyncio.sleep(wait_seconds)
        else:
            source_state["status"] = "failed"
            source_state["finished_at"] = _now()
            source_state["last_error"] = _safe_text(last_error)
            _save_state(state, state_file, report_file)
            print(
                f"source={source_id} failed after {max_retries} attempts: "
                f"{_safe_text(last_error)}",
                flush=True,
            )
            return


async def _run(args: argparse.Namespace) -> None:
    since = _parse_since(args.since)
    selected_ids = set(args.source_id) if args.source_id else None
    connector_types = set(args.connector_type or []) or None
    if connector_types and MANUAL_CONNECTOR in connector_types:
        raise RuntimeError("manual connector is not supported by this batch script")
    if connector_types and X_CONNECTOR in connector_types and not args.include_x:
        raise RuntimeError("selecting x_twitter requires --include-x")
    sources = _active_sources(
        selected_ids,
        connector_types=connector_types,
        include_x=args.include_x,
    )
    if selected_ids and {source.id for source in sources} != selected_ids:
        found = {source.id for source in sources}
        raise RuntimeError(f"requested source IDs are unavailable or excluded: {sorted(selected_ids - found)}")
    if not sources:
        raise RuntimeError("no active non-X sources matched")

    state_file = _state_path(args.state_file)
    report_file = _state_path(args.report_file)
    state = _load_state(
        state_file,
        since=since.isoformat(),
        sources=sources,
        limit=args.limit,
        included_connector_types=sorted({source.connector_type for source in sources}),
        excluded_connector_types=sorted(
            {MANUAL_CONNECTOR} | ({X_CONNECTOR} if not args.include_x else set())
        ),
    )
    state["status"] = "running"
    state["finished_at"] = None
    _save_state(state, state_file, report_file)
    print(
        f"starting raw-only collection: sources={len(sources)} since={since.isoformat()} "
        f"limit={args.limit} state={state_file} report={report_file}",
        flush=True,
    )

    for index, source in enumerate(sources):
        source_state = state["sources"][str(source.id)]
        if source_state.get("status") == "completed":
            print(f"source={source.id} already completed; skipping", flush=True)
            continue
        try:
            await _collect_source(
                source.id,
                source_state,
                since=since,
                limit=args.limit,
                batch_delay=args.batch_delay,
                error_delay=args.error_delay,
                max_retries=args.max_retries,
                state=state,
                state_file=state_file,
                report_file=report_file,
            )
        except Exception as exc:
            source_state["status"] = "failed"
            source_state["finished_at"] = _now()
            source_state["last_error"] = _safe_text(exc)
            _save_state(state, state_file, report_file)
            print(f"source={source.id} setup failed: {_safe_text(exc)}", flush=True)
        if index < len(sources) - 1:
            await asyncio.sleep(args.source_delay)

    failed = any(source.get("status") == "failed" for source in state["sources"].values())
    state["status"] = "completed_with_failures" if failed else "completed"
    state["finished_at"] = _now()
    _save_state(state, state_file, report_file)
    print(f"collection finished: status={state['status']} report={report_file}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect historical connector data into RawItems only, with resumable cursors."
    )
    parser.add_argument("--since", default=DEFAULT_SINCE)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--batch-delay", type=float, default=30.0)
    parser.add_argument("--source-delay", type=float, default=30.0)
    parser.add_argument("--error-delay", type=float, default=60.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--source-id", type=int, action="append")
    parser.add_argument(
        "--connector-type",
        action="append",
        help="restrict collection to one or more connector types (repeatable)",
    )
    parser.add_argument(
        "--include-x",
        action="store_true",
        help="allow local x_twitter collection; X remains excluded by default",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(".run") / DEFAULT_STATE_FILE,
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        default=Path(".run") / DEFAULT_REPORT_FILE,
    )
    args = parser.parse_args()
    args.limit = min(max(args.limit, 1), 50)
    args.batch_delay = max(args.batch_delay, 0.0)
    args.source_delay = max(args.source_delay, 0.0)
    args.error_delay = max(args.error_delay, 1.0)
    args.max_retries = min(max(args.max_retries, 1), 10)
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("collection interrupted; rerun the same command to resume", file=sys.stderr)
        raise SystemExit(130)


if __name__ == "__main__":
    main()
