import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connectors.registry import connector_registry
from app.core.database import get_db
from app.models.connector_run import ConnectorRun
from app.models.source import Source
from app.schemas.connector import (
    ConnectorRegistrationRead,
    ConnectorRunPageRead,
    ConnectorRunRead,
    ConnectorRunRequest,
)
from app.services.connector_runner import ConnectorRunError, run_connector
from app.services.notifications import enqueue_collection_failure

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("", response_model=list[ConnectorRegistrationRead])
def list_registered_connectors() -> list[ConnectorRegistrationRead]:
    return [
        ConnectorRegistrationRead(connector_type=connector_type)
        for connector_type in connector_registry.registered_types()
    ]


@router.get("/runs", response_model=list[ConnectorRunRead])
def list_connector_runs(db: Session = Depends(get_db)) -> list[ConnectorRun]:
    return list(
        db.scalars(select(ConnectorRun).order_by(ConnectorRun.started_at.desc()).limit(100))
    )


@router.get("/runs/page", response_model=ConnectorRunPageRead)
def list_connector_runs_page(
    source_id: int | None = None,
    status_filter: str = Query(
        default="failed",
        alias="status",
        pattern="^(all|completed|failed)$",
    ),
    sort: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conditions = [ConnectorRun.source_id == source_id] if source_id is not None else []
    if status_filter != "all":
        conditions.append(ConnectorRun.status == status_filter)
    total = db.scalar(select(func.count(ConnectorRun.id)).where(*conditions)) or 0
    ordering = (
        (ConnectorRun.started_at.asc(), ConnectorRun.id.asc())
        if sort == "asc"
        else (ConnectorRun.started_at.desc(), ConnectorRun.id.desc())
    )
    statement = (
        select(ConnectorRun)
        .where(*conditions)
        .order_by(*ordering)
        .offset(offset)
        .limit(limit)
    )
    return {"items": list(db.scalars(statement)), "total": total}


@router.post("/{connector_type}/run", response_model=ConnectorRunRead)
async def trigger_connector_run(
    connector_type: str,
    payload: ConnectorRunRequest,
    db: Session = Depends(get_db),
) -> ConnectorRun:
    try:
        return await run_connector(
            db,
            connector_type=connector_type,
            source_id=payload.source_id,
            limit=payload.limit,
            since=payload.since,
            options=payload.options,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConnectorRunError as exc:
        failed_run = db.get(ConnectorRun, exc.run_id) if exc.run_id is not None else None
        if failed_run is not None:
            source = db.get(Source, failed_run.source_id)
            if source is not None:
                try:
                    enqueue_collection_failure(
                        db,
                        source=source,
                        connector_run=failed_run,
                        error=exc.__cause__ or exc,
                        consecutive_failures=1,
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.exception(
                        "failed to enqueue collection alert for connector run id=%s",
                        failed_run.id,
                    )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
