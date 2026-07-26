from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.ocr_lab import OCRProfile, OCRTestRun
from app.schemas.ocr_lab import (
    OCRAssetRead,
    OCRProfileRead,
    OCRTestRequest,
    OCRTestRunRead,
)
from app.services.media_ocr import OCRProcessingError
from app.workflows.ocr_lab import (
    activate_ocr_test_run,
    create_ocr_test_run,
    list_patch_ocr_assets,
)

router = APIRouter()


@router.get("/assets", response_model=list[OCRAssetRead])
def list_ocr_assets(db: Session = Depends(get_db)) -> list[OCRAssetRead]:
    return list_patch_ocr_assets(db)


@router.get("/runs", response_model=list[OCRTestRunRead])
def list_ocr_test_runs(
    media_asset_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[OCRTestRun]:
    statement = select(OCRTestRun).order_by(OCRTestRun.created_at.desc()).limit(100)
    if media_asset_id is not None:
        statement = statement.where(OCRTestRun.media_asset_id == media_asset_id)
    return list(db.scalars(statement))


@router.post(
    "/runs",
    response_model=OCRTestRunRead,
    status_code=status.HTTP_201_CREATED,
)
async def run_ocr_test(
    payload: OCRTestRequest,
    db: Session = Depends(get_db),
) -> object:
    try:
        return await create_ocr_test_run(db, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OCRProcessingError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/profiles", response_model=list[OCRProfileRead])
def list_ocr_profiles(db: Session = Depends(get_db)) -> list[OCRProfile]:
    return list(db.scalars(select(OCRProfile).order_by(OCRProfile.updated_at.desc()).limit(100)))


@router.post("/runs/{run_id}/activate", response_model=OCRProfileRead)
def activate_test_run(
    run_id: int,
    db: Session = Depends(get_db),
) -> OCRProfile:
    test_run = db.get(OCRTestRun, run_id)
    if not test_run:
        raise HTTPException(status_code=404, detail="OCR test run not found")
    return activate_ocr_test_run(db, test_run)
