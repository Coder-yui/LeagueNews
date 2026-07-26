import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.content_blocks import has_quoted_post, text_from_content_blocks
from app.core.config import settings
from app.models.media_extraction import MediaExtraction
from app.models.normalized_item import NormalizedItem, NormalizedItemMediaExtraction
from app.models.raw_item import RawItem
from app.models.workflow import GlossaryTerm, KnowledgeRule, ProcessingRun, ReviewTask
from app.schemas.workflow import OCRReviewCorrection, ReviewRejection
from app.services.llm import LLMClient
from app.workflows.translate_item import build_translation
from app.workflows.understand_media import (
    extraction_context,
    is_patch_preview,
    structure_patch_extraction,
    understand_patch_media,
)

RELEVANCE_STAGE = "relevance"
OCR_STAGE = "image_ocr"
ITEM_STAGE = "item_analysis"

OFFICIAL_CONNECTORS = {"riot_official", "tencent_lol"}
OFFICIAL_ACCOUNTS = {
    "leagueoflegends",
    "lolesports",
    "riotphroxzon",
    "5756404150",
    "5720474518",
}


async def start_item_processing(
    db: Session,
    raw_item: RawItem,
    *,
    supersedes_run_id: int | None = None,
) -> ProcessingRun:
    if raw_item.normalized_item:
        raise ValueError("raw item already has an approved normalized item")
    active = db.scalar(
        select(ProcessingRun).where(
            ProcessingRun.raw_item_id == raw_item.id,
            ProcessingRun.workflow_type == "item",
            ProcessingRun.status.in_(["running", "awaiting_review"]),
        )
    )
    if active:
        raise ValueError(f"raw item already has active processing run {active.id}")
    if supersedes_run_id is None:
        previous = db.scalar(
            select(ProcessingRun)
            .where(
                ProcessingRun.raw_item_id == raw_item.id,
                ProcessingRun.workflow_type == "item",
            )
            .order_by(ProcessingRun.id.desc())
            .limit(1)
        )
        supersedes_run_id = previous.id if previous else None
    run = ProcessingRun(
        raw_item_id=raw_item.id,
        supersedes_run_id=supersedes_run_id,
        workflow_type="item",
        status="running",
        current_stage=RELEVANCE_STAGE,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    await _generate_relevance_review(db, run)
    return run


async def retry_processing_run(db: Session, run: ProcessingRun) -> ProcessingRun:
    if run.workflow_type != "item":
        raise ValueError("only item processing runs can be restarted")
    if run.status not in {"rejected", "failed"}:
        raise ValueError(f"processing run cannot restart from status={run.status}")
    if run.raw_item.normalized_item:
        raise ValueError("raw item already has an approved normalized item")
    active = db.scalar(
        select(ProcessingRun).where(
            ProcessingRun.raw_item_id == run.raw_item_id,
            ProcessingRun.workflow_type == "item",
            ProcessingRun.status.in_(["running", "awaiting_review"]),
        )
    )
    if active:
        raise ValueError(f"raw item already has active processing run {active.id}")

    restarted = ProcessingRun(
        raw_item_id=run.raw_item_id,
        supersedes_run_id=run.id,
        workflow_type="item",
        status="running",
        current_stage=run.current_stage,
        context=dict(run.context),
    )
    db.add(restarted)
    db.commit()
    db.refresh(restarted)

    if restarted.current_stage == RELEVANCE_STAGE:
        await _generate_relevance_review(db, restarted)
    elif restarted.current_stage == OCR_STAGE:
        await _generate_ocr_review(db, restarted)
    elif restarted.current_stage == ITEM_STAGE:
        await _generate_item_review(db, restarted)
    else:
        raise ValueError(f"unsupported restart stage: {restarted.current_stage}")
    return restarted


async def approve_review(
    db: Session,
    review: ReviewTask,
    *,
    note: str | None,
) -> ProcessingRun:
    _require_pending_review(review)
    now = datetime.now(UTC)
    review.status = "approved"
    review.feedback = {"note": note} if note else {}
    review.resolved_at = now
    run = review.processing_run

    if review.stage == RELEVANCE_STAGE:
        if bool(review.proposal.get("is_lol_relevant")):
            run.status = "running"
            if is_patch_preview(run.raw_item):
                run.current_stage = OCR_STAGE
                db.commit()
                await _generate_ocr_review(db, run)
            else:
                run.current_stage = ITEM_STAGE
                db.commit()
                await _generate_item_review(db, run)
        else:
            run.status = "completed"
            run.outcome = "irrelevant"
            run.completed_at = now
            db.commit()
    elif review.stage == OCR_STAGE:
        extraction_ids = _extraction_ids(review.proposal)
        media_extractions = list(
            db.scalars(
                select(MediaExtraction)
                .where(MediaExtraction.id.in_(extraction_ids))
                .order_by(MediaExtraction.id)
            )
        )
        if len(media_extractions) != len(extraction_ids):
            raise ValueError("OCR review references missing media extractions")
        for extraction in media_extractions:
            structure_patch_extraction(extraction, title=run.raw_item.display_title)
        run.context = {
            **run.context,
            "approved_media_extraction_ids": extraction_ids,
        }
        run.status = "running"
        run.current_stage = ITEM_STAGE
        db.commit()
        await _generate_item_review(db, run)
    elif review.stage == ITEM_STAGE:
        _apply_normalized_item(db, run.raw_item, review.proposal)
        run.status = "completed"
        run.outcome = "approved"
        run.completed_at = now
        db.commit()
    else:
        raise ValueError(f"unsupported review stage: {review.stage}")
    db.refresh(run)
    return run


def reject_review(
    db: Session,
    review: ReviewTask,
    *,
    payload: ReviewRejection,
) -> ProcessingRun:
    _require_pending_review(review)
    _validate_rejection(review.stage, payload)
    now = datetime.now(UTC)
    review.status = "rejected"
    review.feedback = payload.model_dump(mode="json")
    review.resolved_at = now
    run = review.processing_run
    run.status = "rejected"
    run.outcome = "review_rejected"
    run.current_stage = review.stage
    run.completed_at = now

    if payload.feedback_type in {"relevance_correction", "analysis_correction"}:
        db.add(
            KnowledgeRule(
                knowledge_type=(
                    "relevance"
                    if payload.feedback_type == "relevance_correction"
                    else "analysis"
                ),
                scope=payload.knowledge_scope,
                rule_text=payload.knowledge_rule or payload.reason,
                correction_data=payload.corrected_values,
                source_review_id=review.id,
                is_active=True,
            )
        )
    elif payload.feedback_type == "translation_term":
        for correction in payload.glossary_updates:
            db.add(
                GlossaryTerm(
                    source_term=correction.source_term,
                    preferred_translation=correction.preferred_translation,
                    forbidden_translations=correction.forbidden_translations,
                    scope=correction.scope,
                    notes=correction.notes or payload.reason,
                    source_review_id=review.id,
                    is_active=True,
                )
            )
    db.commit()
    db.refresh(run)
    return run


async def correct_ocr_review(
    db: Session,
    review: ReviewTask,
    *,
    payload: OCRReviewCorrection,
) -> ProcessingRun:
    _require_pending_review(review)
    if review.stage != OCR_STAGE:
        raise ValueError("OCR correction is only available during image OCR review")
    run = review.processing_run
    approved_ids = _extraction_ids(review.proposal)
    if payload.extraction_id not in approved_ids:
        raise ValueError("media extraction is not part of this review proposal")
    original = db.get(MediaExtraction, payload.extraction_id)
    if not original or original.media_asset.raw_item_id != run.raw_item_id:
        raise ValueError("media extraction does not belong to this raw item")

    corrected_table = payload.table_data.model_dump(mode="json")
    processing_config = dict(original.processing_config)
    processing_config.update(
        {
            "table_data": corrected_table,
            "structure_confidence": 1.0,
            "manual_correction": {
                "corrected_from_extraction_id": original.id,
                "source_review_id": review.id,
                "note": payload.note,
                "corrected_at": datetime.now(UTC).isoformat(),
            },
        }
    )
    corrected = MediaExtraction(
        media_asset_id=original.media_asset_id,
        task_type=original.task_type,
        provider="manual-table-correction",
        ocr_engine=original.ocr_engine,
        structuring_model="",
        schema_version="v2-ocr-review-manual",
        status="processed",
        raw_ocr_text=original.raw_ocr_text,
        ocr_lines=original.ocr_lines,
        structured_data={},
        processing_config=processing_config,
        confidence=original.confidence,
    )
    db.add(corrected)
    db.flush()

    replacement_ids = [
        corrected.id if extraction_id == original.id else extraction_id
        for extraction_id in approved_ids
    ]
    correction_history = list(review.proposal.get("ocr_corrections", []))
    correction_history.append(
        {
            "corrected_from_extraction_id": original.id,
            "corrected_extraction_id": corrected.id,
            "note": payload.note,
        }
    )
    _replace_pending_review(
        db,
        run=run,
        stage=OCR_STAGE,
        proposal={
            "approved_media_extraction_ids": replacement_ids,
            "ocr_corrections": correction_history,
        },
    )
    db.commit()
    db.refresh(run)
    return run


async def _generate_relevance_review(db: Session, run: ProcessingRun) -> None:
    raw_item = run.raw_item
    try:
        result = await LLMClient().judge_relevance(
            title=raw_item.display_title,
            content=text_from_content_blocks(raw_item.content_blocks),
            source_context=_source_context(raw_item),
            knowledge_rules=_knowledge_texts(db, "relevance", raw_item),
        )
        _replace_pending_review(
            db,
            run=run,
            stage=RELEVANCE_STAGE,
            proposal=result.model_dump(mode="json"),
        )
        db.commit()
    except Exception as exc:
        _mark_failed(db, run, exc)
        raise


async def _generate_ocr_review(db: Session, run: ProcessingRun) -> None:
    try:
        media_extractions = await understand_patch_media(
            db,
            run.raw_item,
            force=bool(
                db.scalar(
                    select(func.count(ReviewTask.id)).where(
                        ReviewTask.processing_run_id == run.id,
                        ReviewTask.stage == OCR_STAGE,
                    )
                )
            ),
            structure=False,
            enforce_confidence=False,
        )
        if not media_extractions:
            run.status = "running"
            run.current_stage = ITEM_STAGE
            db.commit()
            await _generate_item_review(db, run)
            return
        _replace_pending_review(
            db,
            run=run,
            stage=OCR_STAGE,
            proposal={
                "approved_media_extraction_ids": [
                    extraction.id for extraction in media_extractions
                ],
                "ocr_corrections": [],
            },
        )
        db.commit()
    except Exception as exc:
        _mark_failed(db, run, exc)
        raise


async def _generate_item_review(db: Session, run: ProcessingRun) -> None:
    try:
        extraction_ids = _extraction_ids(run.context)
        media_extractions = list(
            db.scalars(
                select(MediaExtraction)
                .where(MediaExtraction.id.in_(extraction_ids))
                .order_by(MediaExtraction.id)
            )
        )
        proposal = await _build_item_proposal(
            raw_item=run.raw_item,
            media_extractions=media_extractions,
            glossary=_glossary_payload(db),
            rules=_knowledge_texts(db, "analysis", run.raw_item),
        )
        _replace_pending_review(db, run=run, stage=ITEM_STAGE, proposal=proposal)
        db.commit()
    except Exception as exc:
        _mark_failed(db, run, exc)
        raise


async def _build_item_proposal(
    *,
    raw_item: RawItem,
    media_extractions: list[MediaExtraction],
    glossary: list[dict[str, object]],
    rules: list[str],
    ocr_corrections: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    structured_context = extraction_context(media_extractions)
    source_text = text_from_content_blocks(raw_item.content_blocks)
    analysis_content = source_text
    if structured_context:
        analysis_content += "\n\n[图片版本改动结构化提取]\n" + json.dumps(
            structured_context,
            ensure_ascii=False,
        )
    analysis = await LLMClient().analyze(
        title=raw_item.display_title,
        content=analysis_content,
        source_context=_source_context(raw_item),
        knowledge_rules=rules,
    )
    analysis_entities = [
        entity.model_dump(mode="json") for entity in analysis.entities
    ]
    translation = await build_translation(
        raw_item,
        canonical_title=analysis.title,
        summary=analysis.summary,
        entities=analysis_entities,
        media_extractions=media_extractions,
        glossary=glossary,
    )
    return {
        "normalized_title": translation.translated_title,
        "normalized_text": source_text,
        "summary": translation.translated_summary,
        "category": analysis.category,
        "entities": _normalize_entities(translation.translated_entities),
        "importance_score": analysis.importance_score,
        "credibility": analysis.credibility,
        "credibility_score": analysis.credibility_score,
        "credibility_evidence": analysis.credibility_evidence,
        "language": raw_item.language,
        "source_language": translation.source_language,
        "target_language": translation.target_language,
        "translated_title": translation.translated_title,
        "translated_text": translation.translated_text,
        "translated_content_blocks": translation.translated_content_blocks,
        "translation_status": translation.translation_status,
        "translation_model": translation.translation_model,
        "analysis_model": settings.model_name,
        "analysis_version": "v4-reviewed-item",
        "approved_media_extraction_ids": [item.id for item in media_extractions],
        "media_extractions": structured_context,
        "translated_media_extractions": translation.translated_media_extractions,
        "ocr_corrections": ocr_corrections or [],
        "glossary_term_ids": [
            int(term["id"]) for term in glossary if isinstance(term.get("id"), int)
        ],
    }


def _apply_normalized_item(
    db: Session,
    raw_item: RawItem,
    proposal: dict[str, Any],
) -> NormalizedItem:
    if raw_item.normalized_item:
        raise ValueError("raw item already has an approved normalized item")
    allowed_fields = {
        "normalized_title",
        "normalized_text",
        "summary",
        "category",
        "entities",
        "importance_score",
        "credibility",
        "credibility_score",
        "credibility_evidence",
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
    item = NormalizedItem(
        raw_item_id=raw_item.id,
        **{
            key: (
                _normalize_entities(value)
                if key == "entities" and isinstance(value, list)
                else value
            )
            for key, value in proposal.items()
            if key in allowed_fields
        },
    )
    db.add(item)
    db.flush()

    translated_by_id = {
        int(value["extraction_id"]): value
        for value in proposal.get("translated_media_extractions", [])
        if isinstance(value, dict) and isinstance(value.get("extraction_id"), int)
    }
    for extraction_id in _extraction_ids(proposal):
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
    db.flush()
    return item


def _replace_pending_review(
    db: Session,
    *,
    run: ProcessingRun,
    stage: str,
    proposal: dict[str, Any],
) -> ReviewTask:
    for review in run.reviews:
        if review.status == "pending":
            review.status = "superseded"
            review.resolved_at = datetime.now(UTC)
    review = ReviewTask(
        processing_run_id=run.id,
        stage=stage,
        status="pending",
        proposal=proposal,
    )
    db.add(review)
    run.status = "awaiting_review"
    run.current_stage = stage
    run.error_message = None
    return review


def _validate_rejection(stage: str, payload: ReviewRejection) -> None:
    allowed = {
        RELEVANCE_STAGE: {"relevance_correction"},
        OCR_STAGE: {"ocr_error"},
        ITEM_STAGE: {"analysis_correction", "translation_term"},
    }
    if payload.feedback_type not in allowed.get(stage, set()):
        raise ValueError(
            f"feedback_type={payload.feedback_type} is invalid for stage={stage}"
        )
    if payload.feedback_type == "translation_term" and not payload.glossary_updates:
        raise ValueError("translation rejection requires at least one glossary update")


def _extraction_ids(payload: dict[str, Any]) -> list[int]:
    return [
        int(value)
        for value in payload.get("approved_media_extraction_ids", [])
        if isinstance(value, int)
    ]


def _normalize_entities(values: list[object]) -> list[dict[str, str]]:
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
        normalized.append(record)
    return normalized


def _knowledge_texts(db: Session, knowledge_type: str, raw_item: RawItem) -> list[str]:
    scopes = {
        "global",
        f"connector:{raw_item.source.connector_type}",
        f"source:{raw_item.source_id}",
    }
    rules = db.scalars(
        select(KnowledgeRule)
        .where(
            KnowledgeRule.knowledge_type == knowledge_type,
            KnowledgeRule.is_active.is_(True),
            KnowledgeRule.scope.in_(scopes),
        )
        .order_by(KnowledgeRule.updated_at.desc())
        .limit(100)
    )
    return [f"[{rule.scope} v{rule.version}] {rule.rule_text}" for rule in rules]


def _glossary_payload(db: Session) -> list[dict[str, object]]:
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
    ]


def _source_context(raw_item: RawItem) -> dict[str, object]:
    return {
        "source_id": raw_item.source_id,
        "source_name": raw_item.source.name,
        "connector_type": raw_item.source.connector_type,
        "external_key": raw_item.source.external_key,
        "authority": _source_authority(raw_item),
        "published_at": raw_item.published_at.isoformat() if raw_item.published_at else None,
        "is_repost": has_quoted_post(raw_item.content_blocks),
    }


def _source_authority(raw_item: RawItem) -> int:
    configured = raw_item.source.connector_config.get("authority_level")
    if isinstance(configured, int):
        return configured
    key = (raw_item.source.external_key or "").casefold()
    if raw_item.source.connector_type in OFFICIAL_CONNECTORS or key in OFFICIAL_ACCOUNTS:
        return 100
    if raw_item.source.connector_type in {"weibo", "x_twitter"}:
        return 60
    if raw_item.source.connector_type == "baidu_tieba":
        return 30
    return 50


def _require_pending_review(review: ReviewTask) -> None:
    if review.status != "pending":
        raise ValueError(f"review task cannot be resolved from status={review.status}")


def _mark_failed(db: Session, run: ProcessingRun, exc: Exception) -> None:
    db.rollback()
    failed = db.get(ProcessingRun, run.id)
    if failed:
        failed.status = "failed"
        failed.outcome = "system_error"
        failed.error_message = str(exc)[:4000]
        failed.completed_at = datetime.now(UTC)
        db.commit()
