from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.domain.message_taxonomy import classification_catalog
from app.models.raw_item import RawItem
from app.models.raw_item_source_payload import RawItemSourcePayload
from app.models.source import Source
from app.services.classification_source import resolve_classification_source


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _raw(
    db: Session,
    *,
    current_official: bool,
    upstream_url: str | None,
) -> RawItem:
    current = Source(
        name="当前账号",
        connector_type="x_twitter",
        external_key="current",
        base_url="https://x.com/current",
        is_official=current_official,
    )
    db.add(current)
    db.flush()
    raw = RawItem(
        source_id=current.id,
        content_blocks=[
            {"type": "embed", "embed_kind": "quoted_post", "source_url": upstream_url}
        ]
        if upstream_url
        else [{"type": "paragraph", "text": "RT @unknown: content"}],
    )
    db.add(raw)
    db.flush()
    if upstream_url:
        db.add(
            RawItemSourcePayload(
                raw_item_id=raw.id,
                provider="x_twitter",
                payload={"retweeted_tweet_url": upstream_url},
            )
        )
    db.commit()
    return raw


def test_unofficial_repost_uses_identified_official_upstream() -> None:
    with _session() as db:
        db.add(
            Source(
                name="Riot official",
                connector_type="x_twitter",
                external_key="leagueoflegends",
                base_url="https://x.com/LeagueOfLegends",
                is_official=True,
            )
        )
        raw = _raw(
            db,
            current_official=False,
            upstream_url="https://x.com/LeagueOfLegends/status/123",
        )

        result = resolve_classification_source(db, raw, content_form="repost")

        assert result == {
            "current_source_kind": "unofficial",
            "source_kind": "official",
            "basis": "upstream",
            "upstream_source_url": "https://x.com/LeagueOfLegends/status/123",
        }
        candidates = classification_catalog(
            products=["lol_pc"], source_kind=result["source_kind"]
        )
        assert "game_patch_notes" in {
            value["code"] for value in candidates["message_types"]
        }


def test_official_repost_uses_identified_unofficial_upstream() -> None:
    with _session() as db:
        db.add(
            Source(
                name="Community",
                connector_type="x_twitter",
                external_key="community",
                base_url="https://x.com/community",
                is_official=False,
            )
        )
        raw = _raw(
            db,
            current_official=True,
            upstream_url="https://x.com/community/status/456",
        )
        result = resolve_classification_source(db, raw, content_form="repost")
        assert result["source_kind"] == "unofficial"
        assert result["basis"] == "upstream"
        candidates = classification_catalog(
            products=["lol_pc"], source_kind=result["source_kind"]
        )
        assert "game_patch_notes" not in {
            value["code"] for value in candidates["message_types"]
        }


def test_unidentified_repost_is_unresolved_not_official_evidence() -> None:
    with _session() as db:
        raw = _raw(db, current_official=True, upstream_url=None)
        result = resolve_classification_source(db, raw, content_form="repost")
        assert result["source_kind"] == "unknown"
        assert result["basis"] == "unresolved"
        assert result["upstream_source_url"] is None
        candidates = classification_catalog(
            products=["lol_pc"], source_kind=result["source_kind"]
        )
        codes = {value["code"] for value in candidates["message_types"]}
        assert {"game_patch_notes", "game_community_discussion"} <= codes


def test_quote_and_original_use_current_source_kind() -> None:
    with _session() as db:
        raw = _raw(
            db,
            current_official=False,
            upstream_url="https://x.com/LeagueOfLegends/status/123",
        )
        for content_form in ("quote", "original"):
            result = resolve_classification_source(db, raw, content_form=content_form)
            assert result["source_kind"] == "unofficial"
            assert result["basis"] == "current"
            assert result["upstream_source_url"] is None
            candidates = classification_catalog(
                products=["lol_pc"], source_kind=result["source_kind"]
            )
            assert "game_patch_notes" not in {
                value["code"] for value in candidates["message_types"]
            }
