from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import ConnectorRequest, ConnectorSource
from app.connectors.config import validate_connector_config, validate_external_key
from app.connectors.registry import connector_registry
from app.models.connector_run import ConnectorRun
from app.models.source import Source
from app.services.ingestion import ingest_connector_items


class ConnectorRunError(RuntimeError):
    """A registered connector could not complete a collection run."""


def resolve_source(
    db: Session, *, connector_type: str, source_id: int | None
) -> Source:
    if source_id is not None:
        source = db.get(Source, source_id)
        if not source:
            raise LookupError(f"source not found: {source_id}")
        if source.connector_type != connector_type:
            raise ValueError(
                f"source {source_id} uses connector_type={source.connector_type}, "
                f"not {connector_type}"
            )
        if not source.is_active:
            raise ValueError(f"source is inactive: {source_id}")
        return source

    sources = list(
        db.scalars(
            select(Source).where(
                Source.connector_type == connector_type,
                Source.is_active.is_(True),
            )
        )
    )
    if not sources:
        raise LookupError(f"no active source for connector: {connector_type}")
    if len(sources) > 1:
        raise ValueError("multiple sources match; provide source_id")
    return sources[0]


async def run_connector(
    db: Session,
    *,
    connector_type: str,
    source_id: int | None,
    limit: int,
    since: datetime | None,
    options: dict[str, object],
    cursor: dict[str, object] | None = None,
) -> ConnectorRun:
    if connector_type == "manual":
        raise ValueError("use POST /api/v1/imports/manual for manual imports")
    connector = connector_registry.create(connector_type)
    source = resolve_source(db, connector_type=connector_type, source_id=source_id)
    run = ConnectorRun(
        source_id=source.id,
        connector_type=connector_type,
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    discovered_count = 0
    try:
        source_context = ConnectorSource(
            id=source.id,
            name=source.name,
            connector_type=source.connector_type,
            external_key=validate_external_key(
                source.connector_type, source.external_key
            ),
            base_url=source.base_url,
            connector_config=validate_connector_config(
                source.connector_type, source.connector_config
            ),
        )
        request = ConnectorRequest(
            source=source_context,
            limit=limit,
            since=since,
            options=options,
            cursor=cursor,
        )
        batch = await connector.collect(request)
        discovered_count = len(batch)
        result = await ingest_connector_items(db, source=source, items=list(batch))
        run = db.get(ConnectorRun, run.id)
        if not run:
            raise RuntimeError("connector run record disappeared")
        run.status = "completed"
        run.discovered_count = discovered_count
        run.created_count = len(result.created)
        run.revised_count = len(result.revised)
        run.skipped_count = len(result.skipped)
        run.candidate_count = discovered_count
        run.truncated = batch.truncated
        run.cursor_used = batch.cursor_used
        run.next_cursor = batch.next_cursor
        run.finished_at = datetime.now(UTC)
        db.commit()
        db.refresh(run)
        return run
    except Exception as exc:
        db.rollback()
        failed_run = db.get(ConnectorRun, run.id)
        if failed_run:
            failed_run.status = "failed"
            failed_run.discovered_count = discovered_count
            failed_run.error_message = str(exc)[:4000]
            failed_run.finished_at = datetime.now(UTC)
            db.commit()
        raise ConnectorRunError(str(exc)) from exc
