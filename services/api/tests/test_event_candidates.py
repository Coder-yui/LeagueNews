from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.services.event_aggregation import create_event
from app.services.event_candidates import find_event_candidates, stable_event_key


def _add_item(
    db: Session,
    *,
    source_id: int,
    index: int,
    title: str,
    published_at: datetime,
    entities: list[dict[str, str]] | None = None,
    category: str = "版本更新",
) -> NormalizedItem:
    raw = RawItem(
        source_id=source_id,
        external_id=f"candidate-{index}",
        native_title=title,
        content_blocks=[{"id": "b0001", "type": "paragraph", "text": title}],
        published_at=published_at,
    )
    db.add(raw)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw.id,
        normalized_title=title,
        normalized_text=title,
        summary=title,
        category=category,
        entities=entities or [],
        importance_score=0.5,
        credibility="official",
        credibility_score=1.0,
        credibility_evidence=[],
        target_language="zh-CN",
        translated_title=title,
        translated_content_blocks=[],
        translation_status="not_required",
        analysis_model="test",
        analysis_version="test",
    )
    db.add(item)
    db.commit()
    return item


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


def test_stable_patch_key_requires_version_context(db: Session) -> None:
    source = Source(name="Key Source", connector_type="manual")
    db.add(source)
    db.commit()
    patch = _add_item(
        db,
        source_id=source.id,
        index=1,
        title="Patch 26.13 Full Preview",
        published_at=datetime(2026, 6, 17, tzinfo=UTC),
    )
    unrelated = _add_item(
        db,
        source_id=source.id,
        index=2,
        title="比分 26.13",
        published_at=datetime(2026, 6, 17, tzinfo=UTC),
        category="赛事",
    )

    assert stable_event_key(patch) == "patch:26.13"
    assert stable_event_key(unrelated) is None


def test_candidate_search_returns_exact_patch_match_with_reasons(db: Session) -> None:
    source = Source(name="Candidate Source", connector_type="manual")
    db.add(source)
    db.commit()
    preview = _add_item(
        db,
        source_id=source.id,
        index=1,
        title="26.13 版本预览",
        published_at=datetime(2026, 6, 16, tzinfo=UTC),
        entities=[{"name": "26.13", "type": "patch"}],
    )
    event = create_event(
        db,
        normalized_item_id=preview.id,
        event_key="patch:26.13",
        title="英雄联盟 26.13 版本预览",
        summary="初始预览",
        category="版本更新",
    )
    full_preview = _add_item(
        db,
        source_id=source.id,
        index=2,
        title="26.13 版本完整预览",
        published_at=datetime(2026, 6, 17, tzinfo=UTC),
        entities=[{"name": "26.13", "type": "patch"}],
    )

    first = find_event_candidates(db, normalized_item_id=full_preview.id)
    repeated = find_event_candidates(db, normalized_item_id=full_preview.id)

    assert first == repeated
    assert first[0].event_id == event.id
    assert first[0].score >= 100
    assert any("稳定事件键精确匹配" in reason for reason in first[0].reasons)


def test_candidate_search_can_return_zero_candidates(db: Session) -> None:
    source = Source(name="Zero Source", connector_type="manual")
    db.add(source)
    db.commit()
    existing = _add_item(
        db,
        source_id=source.id,
        index=1,
        title="职业联赛赛果",
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        entities=[{"name": "Team A", "type": "team"}],
        category="赛事",
    )
    create_event(
        db,
        normalized_item_id=existing.id,
        title="一月职业联赛",
        summary="赛果",
        category="赛事",
    )
    incoming = _add_item(
        db,
        source_id=source.id,
        index=2,
        title="全新皮肤上线",
        published_at=datetime(2026, 7, 1, tzinfo=UTC),
        entities=[{"name": "Champion B", "type": "champion"}],
        category="皮肤",
    )

    assert find_event_candidates(db, normalized_item_id=incoming.id) == []


def test_candidate_search_is_ranked_and_capped_at_five(db: Session) -> None:
    source = Source(name="Limit Source", connector_type="manual")
    db.add(source)
    db.commit()
    base_time = datetime(2026, 7, 1, tzinfo=UTC)
    for index in range(6):
        member = _add_item(
            db,
            source_id=source.id,
            index=index,
            title=f"阿狸平衡调整 {index}",
            published_at=base_time - timedelta(days=index),
            entities=[{"name": "阿狸", "type": "champion"}],
            category="英雄平衡",
        )
        create_event(
            db,
            normalized_item_id=member.id,
            title=f"阿狸平衡调整事件 {index}",
            summary="测试",
            category="英雄平衡",
        )
    incoming = _add_item(
        db,
        source_id=source.id,
        index=100,
        title="阿狸平衡调整后续",
        published_at=base_time,
        entities=[{"name": "阿狸", "type": "champion"}],
        category="英雄平衡",
    )

    candidates = find_event_candidates(db, normalized_item_id=incoming.id)

    assert len(candidates) == 5
    assert [candidate.score for candidate in candidates] == sorted(
        (candidate.score for candidate in candidates),
        reverse=True,
    )
    with pytest.raises(ValueError, match="between 1 and 5"):
        find_event_candidates(db, normalized_item_id=incoming.id, limit=6)
