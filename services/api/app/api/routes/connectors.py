from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.registry import connector_registry
from app.core.database import get_db
from app.models.connector_run import ConnectorRun
from app.schemas.connector import (
    ConnectorRegistrationRead,
    ConnectorRunRead,
    ConnectorRunRequest,
)
from app.services.connector_runner import ConnectorRunError, run_connector

router = APIRouter()


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
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
