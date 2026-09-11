"""Per application request accounting, independent of publication transactions.

The ledger contains hashes and usage only. SDK-internal HTTP retries are unknown.
A started/response record without a terminal record is incomplete, never free.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
import hashlib
import json
import sqlite3
import time
from uuid import uuid4


class AttemptRecorder(Protocol):
    def record(self, record: dict[str, object]) -> None: ...


class MemoryRecorder:
    def __init__(self, sink: AttemptRecorder | None = None):
        self.sink = sink
        self.records: dict[tuple[str, str], dict[str, object]] = {}

    def record(self, record):
        if self.sink is not None:
            self.sink.record(record)
        self.records.setdefault((record["attempt_id"], record["status"]), dict(record))

    def attempts(self):
        latest = {}
        for record in self.records.values():
            latest[record["attempt_id"]] = record
        return list(latest.values())


class SQLiteAttemptRecorder:
    """Small local durable ledger, never a connection to the business database."""

    def __init__(self, path: Path):
        self.path = path

    def record(self, record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=10) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS call_attempts (attempt_id TEXT, status TEXT, payload TEXT NOT NULL, PRIMARY KEY(attempt_id, status))"
            )
            connection.execute(
                "INSERT OR IGNORE INTO call_attempts VALUES (?, ?, ?)",
                (record["attempt_id"], record["status"], json.dumps(record, ensure_ascii=False)),
            )


@dataclass(frozen=True)
class TokenPrice:
    version: str
    input_per_million: float
    output_per_million: float

    def __post_init__(self):
        from math import isfinite

        if not self.version or any(
            not isfinite(value) or value < 0
            for value in (self.input_per_million, self.output_per_million)
        ):
            raise ValueError("prices require a version and finite nonnegative rates")


@dataclass
class MeasurementScope:
    recorder: AttemptRecorder
    identity: dict[str, object] = field(default_factory=dict)
    prices: dict[tuple[str, str], TokenPrice] = field(default_factory=dict)
    method_calls: list[dict[str, object]] = field(default_factory=list)


_scope: ContextVar[MeasurementScope | None] = ContextVar("call_measurement_scope", default=None)
_identity: ContextVar[dict[str, object]] = ContextVar("call_identity", default={})


@contextmanager
def measurement_scope(recorder, **identity):
    token = _scope.set(MeasurementScope(recorder, identity))
    try:
        yield _scope.get()
    finally:
        _scope.reset(token)


@contextmanager
def call_identity(**identity):
    token = _identity.set({**_identity.get(), **identity})
    try:
        yield
    finally:
        _identity.reset(token)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


class CallAttempt:
    def __init__(
        self,
        *,
        logical_call_id,
        attempt,
        provider,
        model,
        operation,
        parameters,
        prompt_hash,
        schema_hash,
        input_hash,
        recorder=None,
    ):
        scope = _scope.get()
        if scope is None:
            from app.core.config import settings

            scope = MeasurementScope(
                recorder
                or SQLiteAttemptRecorder(
                    settings._resolve_project_path(settings.call_metering_path)
                )
            )
        self.scope = scope
        self.started = time.perf_counter()
        self.row = {
            "measurement_kind": "provider_attempt",
            "run_id": None,
            "target_entity_type": None,
            "method": operation,
            "implementation": "json_completion",
            "implementation_version": "call-metering-v1",
            **scope.identity,
            **_identity.get(),
            "attempt_id": str(uuid4()),
            "logical_call_id": logical_call_id,
            "attempt_number": attempt,
            "provider": provider,
            "model": model,
            "stage": operation,
            "parameters": parameters,
            "prompt_hash": prompt_hash,
            "schema_hash": schema_hash,
            "input_hash": input_hash,
            "input_tokens": None,
            "output_tokens": None,
            "usage": {},
            "usage_completeness": "unknown",
            "cost_usd": None,
            "price_version": None,
            "cost_completeness": "unknown",
            "request_count_scope": "application_attempt; SDK HTTP retries unobserved",
        }
        self.emit("started")

    def emit(self, status, **extra):
        self.row.update(extra)
        self.row.update(status=status, duration_ms=(time.perf_counter() - self.started) * 1000)
        self.scope.recorder.record(dict(self.row))

    def response(self, response):
        usage = getattr(response, "usage", None)
        usage = usage.model_dump(mode="json") if hasattr(usage, "model_dump") else usage
        usage = usage if isinstance(usage, dict) else {}
        input_tokens, output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
        complete = all(type(value) is int and value >= 0 for value in (input_tokens, output_tokens))
        self.row.update(
            provider_request_id=getattr(response, "_request_id", None),
            response_id=getattr(response, "id", None),
            model=getattr(response, "model", None) or self.row["model"],
            usage=usage,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage_completeness="complete" if complete else "partial" if usage else "unknown",
        )
        price = self.scope.prices.get((self.row["provider"], self.row["model"]))
        if price:
            self.row["price_version"] = price.version
            # Detailed cache/reasoning dimensions need explicit supported prices.
            unsupported = any(value for key, value in usage.items() if key.endswith("_details"))
            if complete and not unsupported:
                self.row.update(
                    cost_usd=(
                        input_tokens * price.input_per_million
                        + output_tokens * price.output_per_million
                    )
                    / 1_000_000,
                    cost_completeness="complete",
                )
        self.emit("response_received")


def summarize_attempts(attempts):
    complete = all(row.get("usage_completeness") == "complete" for row in attempts)
    costs_complete = all(row.get("cost_completeness") == "complete" for row in attempts)
    known_tokens = sum(
        (row.get("input_tokens") or 0) + (row.get("output_tokens") or 0) for row in attempts
    )
    known_cost = sum(row.get("cost_usd") or 0 for row in attempts)
    return {
        "measurement_kinds": {kind: sum(row.get("measurement_kind", "provider_attempt") == kind for row in attempts)
                              for kind in sorted({row.get("measurement_kind", "provider_attempt") for row in attempts})},
        "application_attempt_count": len(attempts),
        "logical_call_count": len({row["logical_call_id"] for row in attempts}),
        "token_count": known_tokens if complete else None,
        "known_token_count": known_tokens,
        "cost_usd": known_cost if costs_complete else None,
        "known_cost_usd": known_cost,
        "unknown_usage_attempts": sum(
            row.get("usage_completeness") != "complete" for row in attempts
        ),
        "unknown_cost_attempts": sum(
            row.get("cost_completeness") != "complete" for row in attempts
        ),
    }


def measured_run(function):
    """Attach durable run identity without putting runtime dependencies in graph state."""
    from functools import wraps

    @wraps(function)
    async def wrapped(self, request, *args, **kwargs):
        identity = {
            "run_id": request.thread_id,
            "invocation_id": str(uuid4()),
            "workflow_run_id": request.workflow_run_id,
            "target_entity_type": "raw_item"
            if hasattr(request, "raw_item_id")
            else "normalized_item"
            if hasattr(request, "normalized_item_id")
            else "daily_report",
            "target_entity_id": getattr(request, "raw_item_id", None)
            or getattr(request, "normalized_item_id", None),
            "target_revision": getattr(request, "raw_item_revision", None)
            or getattr(request, "normalized_item_revision", None),
            "cost_scope": "shared_daily" if "daily" in function.__name__ else "direct",
        }
        with call_identity(**identity):
            return await function(self, request, *args, **kwargs)

    return wrapped


def record_method_call(record):
    scope = _scope.get()
    if scope is not None:
        scope.method_calls.append({**_identity.get(), **record})
