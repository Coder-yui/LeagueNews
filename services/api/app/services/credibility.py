from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.credibility import (
    calibrate_reliability,
    posterior_reliability,
    reliability_prior,
)
from app.models.credibility import SourceReliabilityHistory
from app.models.source import Source


def source_reliability_history(
    db: Session,
    source: Source,
) -> SourceReliabilityHistory:
    history = db.scalar(
        select(SourceReliabilityHistory)
        .where(SourceReliabilityHistory.source_id == source.id)
        .with_for_update()
    )
    if history is not None:
        return history
    configured = source.connector_config.get("authority_level")
    prior = reliability_prior(
        source_name=source.name,
        connector_type=source.connector_type,
        external_key=source.external_key,
        authority=configured if isinstance(configured, int) else None,
    )
    history = SourceReliabilityHistory(
        source_id=source.id,
        confirmed_count=0,
        refuted_count=0,
        alpha=prior.alpha,
        beta=prior.beta,
    )
    db.add(history)
    db.flush()
    return history


def record_source_outcome(
    db: Session,
    *,
    source: Source,
    was_confirmed: bool,
) -> float:
    history = source_reliability_history(db, source)
    history.confirmed_count, history.refuted_count = calibrate_reliability(
        confirmed_count=history.confirmed_count,
        refuted_count=history.refuted_count,
        was_confirmed=was_confirmed,
    )
    return posterior_reliability(
        confirmed_count=history.confirmed_count,
        refuted_count=history.refuted_count,
        alpha=history.alpha,
        beta=history.beta,
    )
