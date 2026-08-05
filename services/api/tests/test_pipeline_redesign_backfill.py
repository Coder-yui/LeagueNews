from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from scripts.backfill_pipeline_redesign import (
    infer_legacy_profile,
    run_backfill,
)


def _add_item(
    db: Session,
    *,
    source: Source,
    external_id: str,
    title: str,
) -> NormalizedItem:
    raw = RawItem(
        source_id=source.id,
        external_id=external_id,
        native_title=title,
        content_blocks=[
            {"id": "b0001", "type": "paragraph", "text": title}
        ],
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    db.add(raw)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw.id,
        normalized_title=title,
        normalized_text=title,
        summary=title,
        category="其他",
        entities=[],
        content_type=None,
        primary_topic="other",
        importance_score=0.4,
        credibility="unverified",
        credibility_score=0.6,
        credibility_evidence=[],
        analysis_model="legacy",
    )
    db.add(item)
    db.flush()
    return item


def test_backfill_infers_real_rumor_gradations_and_is_dry_run_safe() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        park = Source(
            name="召唤师Park",
            connector_type="weibo",
            external_key="2522098777",
        )
        official = Source(
            name="腾讯英雄联盟官方网站",
            connector_type="tencent_lol",
            external_key="lol.qq.com",
        )
        db.add_all([park, official])
        db.flush()
        speculative = _add_item(
            db,
            source=park,
            external_id="speculative",
            title="WBG 打野人选未定，最后官宣为准",
        )
        likely = _add_item(
            db,
            source=park,
            external_id="likely",
            title="WBG 人员变动，我确认下细节下午说",
        )
        confirmed = _add_item(
            db,
            source=official,
            external_id="official",
            title="2026年7月31日不停机更新公告",
        )
        db.commit()

        assert infer_legacy_profile(speculative) == (
            "insider_rumor",
            "roster",
            "speculative",
        )
        assert infer_legacy_profile(likely) == (
            "insider_confirmed",
            "roster",
            "likely",
        )
        assert infer_legacy_profile(confirmed) == (
            "official_notice",
            "patch",
            "confirmed",
        )

        dry_run = run_backfill(db, apply=False)
        assert dry_run.items_scored == 3
        db.expire_all()
        assert db.get(NormalizedItem, speculative.id).content_type is None

        applied = run_backfill(db, apply=True)
        assert applied.items_scored == 3
        db.expire_all()
        scores = [
            db.get(NormalizedItem, item.id).credibility_score
            for item in (speculative, likely, confirmed)
        ]
        assert scores == [0.268125, 0.48, 0.95]
        assert scores[0] < scores[1] < scores[2]
        assert {
            "source_reliability",
            "statement_certainty",
            "content_type_prior",
            "staleness",
        } <= db.get(
            NormalizedItem,
            confirmed.id,
        ).credibility_components.keys()
