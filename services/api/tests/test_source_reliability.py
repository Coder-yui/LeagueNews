import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.api.routes.sources import update_source_reliability
from app.core.database import Base
from app.models.source import Source
from app.schemas.source import SourceRead, SourceReliabilityUpdate


def test_source_reliability_is_validated_persisted_and_serialized() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Editable source")
        db.add(source)
        db.commit()
        updated = update_source_reliability(
            source.id,
            SourceReliabilityUpdate(is_official=True, reliability_score=0.85),
            db,
        )
        payload = SourceRead.model_validate(updated)
        assert payload.is_official is True
        assert payload.reliability_score == 0.85


def test_source_reliability_range_is_enforced_by_schema_and_database() -> None:
    with pytest.raises(ValidationError):
        SourceReliabilityUpdate(is_official=False, reliability_score=1.01)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Source(name="Invalid source", reliability_score=-0.1))
        with pytest.raises(IntegrityError):
            db.commit()
