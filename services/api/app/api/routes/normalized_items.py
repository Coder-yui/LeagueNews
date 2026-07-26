from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.normalized_item import NormalizedItem
from app.schemas.normalized_item import NormalizedItemRead

router = APIRouter()


@router.get("", response_model=list[NormalizedItemRead])
def list_normalized_items(db: Session = Depends(get_db)) -> list[NormalizedItem]:
    statement = select(NormalizedItem).order_by(NormalizedItem.created_at.desc()).limit(100)
    return list(db.scalars(statement))
