"""Read-only evidence and context preparation for message methods.

This module is deliberately independent from workflow orchestration.  It owns
the serializable snapshots that methods consume, while callers own the short
database session used to create them.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.content_blocks import has_repost_evidence, text_from_content_blocks
from app.models.media_asset import MediaAsset
from app.models.raw_item import RawItem
from app.models.workflow import GlossaryTerm, KnowledgeRule


def raw_item_statement(raw_item_id: int):
    return (
        select(RawItem)
        .where(RawItem.id == raw_item_id)
        .options(
            selectinload(RawItem.source),
            selectinload(RawItem.source_payload),
            selectinload(RawItem.media_assets).selectinload(MediaAsset.extractions),
            selectinload(RawItem.normalized_item),
        )
    )


def load_raw_item(db: Session, raw_item_id: int) -> RawItem:
    raw_item = db.scalar(raw_item_statement(raw_item_id))
    if raw_item is None:
        raise ValueError(f"raw item {raw_item_id} not found")
    return raw_item


def analysis_content(translation_proposal: dict[str, Any]) -> str:
    translated_blocks = list(translation_proposal.get("translated_content_blocks") or [])
    content = text_from_content_blocks(translated_blocks)
    translated_structures = [
        value.get("translated_data")
        for value in translation_proposal.get("translated_media_extractions", [])
        if isinstance(value, dict) and isinstance(value.get("translated_data"), dict)
    ]
    if translated_structures:
        import json

        content += "\n\n[图片版本改动结构化中文译文]\n" + json.dumps(
            translated_structures,
            ensure_ascii=False,
        )
    return content


def message_analysis_content(translation_proposal: dict[str, Any]) -> str:
    title = str(translation_proposal.get("translated_title") or "").strip()
    body = analysis_content(translation_proposal).strip()
    sections = []
    if title:
        sections.append(f"[消息标题]\n{title}")
    if body:
        sections.append(f"[消息正文]\n{body}")
    return "\n\n".join(sections)


def importance_scoring_content(
    translation_proposal: dict[str, Any],
    fact_proposal: dict[str, Any],
) -> str:
    return "\n".join(
        value
        for value in (
            str(fact_proposal.get("title") or "").strip(),
            analysis_content(translation_proposal).strip(),
        )
        if value
    )


def knowledge_rule_snapshot(
    db: Session, knowledge_type: str, raw_item: RawItem
) -> list[dict[str, object]]:
    scopes = {
        "global",
        f"connector:{raw_item.source.connector_type}",
        f"source:{raw_item.source_id}",
    }
    rules = db.scalars(
        select(KnowledgeRule)
        .where(
            KnowledgeRule.knowledge_type == knowledge_type,
            KnowledgeRule.lifecycle_status == "active",
            KnowledgeRule.scope.in_(scopes),
        )
        .order_by(KnowledgeRule.updated_at.desc())
        .limit(100)
    )
    return [
        {
            "id": rule.id,
            "version": rule.version,
            "scope": rule.scope,
            "rule_text": rule.rule_text,
        }
        for rule in rules
    ]


def knowledge_texts_from_snapshot(
    rules: list[dict[str, object]],
) -> list[str]:
    return [f"[{rule['scope']} v{rule['version']}] {rule['rule_text']}" for rule in rules]


def glossary_payload(db: Session, source_text: str = "") -> list[dict[str, object]]:
    normalized_text = source_text.casefold()
    terms = db.scalars(
        select(GlossaryTerm)
        .where(GlossaryTerm.is_active.is_(True))
        .order_by(GlossaryTerm.updated_at.desc())
        .limit(500)
    )
    return [
        {
            "id": term.id,
            "source_term": term.source_term,
            "preferred_translation": term.preferred_translation,
            "forbidden_translations": term.forbidden_translations,
            "scope": term.scope,
            "notes": term.notes,
            "version": term.version,
        }
        for term in terms
        if not normalized_text or term.source_term.casefold() in normalized_text
    ]


def source_context(raw_item: RawItem) -> dict[str, object]:
    return {
        "source_id": raw_item.source_id,
        "source_name": raw_item.source.name,
        "connector_type": raw_item.source.connector_type,
        "external_key": raw_item.source.external_key,
        "authority": source_authority(raw_item),
        "published_at": raw_item.published_at.isoformat() if raw_item.published_at else None,
        "is_repost": has_repost_evidence(raw_item.content_blocks),
    }


def source_authority(raw_item: RawItem) -> int:
    configured = raw_item.source.connector_config.get("authority_level")
    if isinstance(configured, int):
        return configured
    if raw_item.source.is_official:
        return 100
    if raw_item.source.connector_type in {"weibo", "x_twitter"}:
        return 60
    if raw_item.source.connector_type == "baidu_tieba":
        return 60
    return 50
