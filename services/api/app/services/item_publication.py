"""Application operations for message publication and revision creation.

Methods produce proposals.  This module is the only V3 item boundary that
turns an approved proposal into a NormalizedItem projection and revision.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.methods import MethodAssembly
from app.domain.importance import IMPORTANCE_POLICY_VERSION
from app.domain.message_entities import normalize_entities
from app.domain.message_taxonomy import CLASSIFICATION_VERSION
from app.models.normalized_item import (
    NormalizedItem,
    NormalizedItemMediaExtraction,
    NormalizedItemRevision,
)
from app.models.media_asset import MediaAsset
from app.models.media_extraction import MediaExtraction
from app.models.raw_item import RawItem
from app.services.media_publication import publish_raw_item_media
from app.services.notifications import enqueue_featured_message


def approved_media_extraction_ids(proposal: dict[str, Any]) -> list[int]:
    return [
        int(value)
        for value in proposal.get("approved_media_extraction_ids", [])
        if isinstance(value, int)
    ]


def normalize_publication_entities(values: list[object]) -> list[dict[str, str]]:
    """Keep the V2 persisted entity shape while the taxonomy evolves separately."""

    type_aliases = {
        "英雄": "champion",
        "物品": "item",
        "装备": "item",
        "设计师": "person",
        "人物": "person",
        "赛事": "tournament",
        "版本": "patch",
    }
    normalized: list[dict[str, str]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        name = value.get("name")
        entity_type = value.get("type")
        canonical_name = value.get("canonical_name")
        if not isinstance(name, str) or not name.strip():
            dynamic_fields = [
                (key, field_value)
                for key, field_value in value.items()
                if isinstance(field_value, str) and field_value.strip()
            ]
            if not dynamic_fields:
                continue
            dynamic_type, name = dynamic_fields[0]
            entity_type = type_aliases.get(str(dynamic_type), str(dynamic_type))
        record = {
            "name": name.strip(),
            "type": (
                entity_type.strip()
                if isinstance(entity_type, str) and entity_type.strip()
                else "other"
            ),
        }
        if isinstance(canonical_name, str) and canonical_name.strip():
            record["canonical_name"] = canonical_name.strip()
        role = str(value.get("role") or "context").casefold()
        record["role"] = role if role in {"core", "context", "affected"} else "context"
        normalized.append(record)
    return normalized


def normalized_title(
    *,
    raw_item: RawItem,
    translation_proposal: dict[str, Any],
    analysis_proposal: dict[str, Any],
) -> str:
    for candidate in (
        analysis_proposal.get("title"),
        translation_proposal.get("translated_title"),
        raw_item.native_title,
    ):
        value = str(candidate or "").strip()
        if value:
            return value[:500]
    return {
        "media_only": "仅媒体消息",
        "link_only": "仅链接消息",
    }.get(str(analysis_proposal.get("content_form") or ""), "未命名消息")


def build_item_proposal(
    *,
    raw_item: RawItem,
    translation_proposal: dict[str, Any],
    analysis_proposal: dict[str, Any],
    importance_proposal: dict[str, Any] | None,
    relevance_proposal: dict[str, Any] | None = None,
    evidence_gate: dict[str, Any] | None = None,
    knowledge_snapshot: list[dict[str, object]] | None = None,
    ocr_corrections: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    classification = analysis_proposal
    importance = importance_proposal or {
        "message_type": "unknown",
        "topics": ["unknown"],
        "importance_score": 0.0,
        "importance_evidence": [],
        "importance_dimensions": {},
        "importance_policy_version": IMPORTANCE_POLICY_VERSION,
        "importance_calculation": {"skipped_reason": classification.get("content_form")},
        "priority_score": 0.0,
        "priority_calculation": {"skipped_reason": classification.get("content_form")},
    }
    classification_source = dict(
        classification.get("classification_source")
        or importance.get("classification_source")
        or {}
    )
    return {
        **translation_proposal,
        "normalized_title": normalized_title(
            raw_item=raw_item,
            translation_proposal=translation_proposal,
            analysis_proposal=classification,
        ),
        "summary": str(classification.get("summary") or ""),
        "entities": normalize_entities(
            [
                dict(entity)
                for entity in classification.get("entities", [])
                if isinstance(entity, dict)
            ]
        ),
        "products": list(classification.get("products") or ["unknown"]),
        "message_type": str(importance.get("message_type") or "unknown"),
        "topics": list(importance.get("topics") or ["unknown"]),
        "classification_version": str(
            classification.get("classification_version") or CLASSIFICATION_VERSION
        ),
        "content_form": classification["content_form"],
        "facets": {
            "products": list(classification.get("products") or ["unknown"]),
            "message_type": str(importance.get("message_type") or "unknown"),
            "classification_source": classification_source,
            "evidence_gate": dict(evidence_gate or {}),
            "relevance": dict(relevance_proposal or {}),
        },
        **importance,
        "language": raw_item.language,
        "analysis_model": settings.model_name,
        "analysis_version": "message-processing-v1.1",
        "execution_metadata": {
            **dict(classification.get("_execution_metadata") or {}),
            **dict(importance.get("_execution_metadata") or {}),
        },
        # Kept only as a read-side bridge for old V2 checkpoint snapshots.
        "_execution_metadata": {
            **dict(classification.get("_execution_metadata") or {}),
            **dict(importance.get("_execution_metadata") or {}),
        },
        "knowledge_rules": knowledge_snapshot or [],
        "ocr_corrections": ocr_corrections or [],
    }


def apply_normalized_item(
    db: Session,
    raw_item: RawItem,
    proposal: dict[str, Any],
    *,
    method_assembly: MethodAssembly,
    processing_run_id: int | None = None,
) -> NormalizedItem:
    """Apply an already validated proposal inside the caller's transaction."""

    allowed_fields = {
        "normalized_title",
        "normalized_text",
        "summary",
        "entities",
        "products",
        "message_type",
        "topics",
        "classification_version",
        "content_form",
        "facets",
        "importance_score",
        "importance_dimensions",
        "importance_policy_version",
        "importance_calculation",
        "priority_score",
        "priority_calculation",
        "language",
        "source_language",
        "target_language",
        "translated_title",
        "translated_text",
        "translated_content_blocks",
        "translation_status",
        "translation_model",
        "analysis_model",
        "analysis_version",
    }
    values = {
        key: (
            normalize_publication_entities(value)
            if key == "entities" and isinstance(value, list)
            else value
        )
        for key, value in proposal.items()
        if key in allowed_fields
    }
    item = raw_item.normalized_item
    if item is None:
        item = NormalizedItem(raw_item_id=raw_item.id, **values)
        db.add(item)
    elif item.publication_status != "withdrawn":
        raise ValueError("raw item already has an approved normalized item")
    else:
        for key, value in values.items():
            setattr(item, key, value)
        item.current_revision += 1
        item.publication_status = "published"
        item.withdrawn_at = None
        item.withdrawal_reason = None
        for link in list(item.media_links):
            db.delete(link)
    db.flush()
    publish_raw_item_media(raw_item)
    enqueue_featured_message(db, item, assembly=method_assembly)

    translated_by_id = {
        int(value["extraction_id"]): value
        for value in proposal.get("translated_media_extractions", [])
        if isinstance(value, dict) and isinstance(value.get("extraction_id"), int)
    }
    extraction_ids = approved_media_extraction_ids(proposal)
    if extraction_ids:
        production_extractions = list(
            db.scalars(
                select(MediaExtraction)
                .join(MediaAsset, MediaAsset.id == MediaExtraction.media_asset_id)
                .where(
                    MediaExtraction.id.in_(extraction_ids),
                    MediaAsset.raw_item_id == raw_item.id,
                    MediaExtraction.artifact_scope == "production",
                    MediaExtraction.status == "processed",
                )
            )
        )
        if {extraction.id for extraction in production_extractions} != set(extraction_ids):
            raise ValueError("publication can only reference processed production media artifacts")
    for extraction_id in extraction_ids:
        translated = translated_by_id.get(extraction_id, {})
        db.add(
            NormalizedItemMediaExtraction(
                normalized_item_id=item.id,
                media_extraction_id=extraction_id,
                translated_structured_data=dict(translated.get("translated_data") or {}),
                translation_status=str(proposal["translation_status"]),
                translation_model=proposal.get("translation_model"),
            )
        )
    db.add(
        NormalizedItemRevision(
            normalized_item_id=item.id,
            revision=item.current_revision,
            snapshot={
                **values,
                "approved_media_extraction_ids": extraction_ids,
                "translated_media_extractions": proposal.get("translated_media_extractions", []),
            },
            processing_run_id=processing_run_id,
            change_note=(
                "corrected and republished" if item.current_revision > 1 else "initial publication"
            ),
        )
    )
    db.flush()
    return item
