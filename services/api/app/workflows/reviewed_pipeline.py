import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.content_blocks import has_quoted_post, text_from_content_blocks
from app.core.config import settings
from app.domain.importance import (
    DIMENSIONS,
    IMPORTANCE_POLICY_VERSION,
    calculate_importance,
)
from app.domain.ontology import ONTOLOGY_VERSION, normalize_entities, topic_from_category
from app.models.media_extraction import MediaExtraction
from app.models.normalized_item import (
    NormalizedItem,
    NormalizedItemMediaExtraction,
    NormalizedItemRevision,
)
from app.models.pipeline import PipelineCorrection, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.workflow import GlossaryTerm, KnowledgeRule, ProcessingRun, ReviewTask
from app.schemas.workflow import OCRReviewCorrection, ReviewRejection
from app.services.llm import LLMClient, execution_metadata
from app.services.claims import extract_traceable_claim
from app.services.media_publication import publish_raw_item_media
from app.services.pipeline_execution import (
    PipelineExecutionGuard,
    assert_execution_owned,
)
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
TRANSLATION_STAGE = "translation"

OFFICIAL_CONNECTORS = {"riot_official", "tencent_lol"}
OFFICIAL_ACCOUNTS = {
    "leagueoflegends",
    "lolesports",
    "riotphroxzon",
    "5756404150",
    "5720474518",
}


def _guard_kwargs(
    execution_guard: PipelineExecutionGuard | None,
) -> dict[str, PipelineExecutionGuard]:
    return (
        {"execution_guard": execution_guard}
        if execution_guard is not None
        else {}
    )


async def start_item_processing(
    db: Session,
    raw_item: RawItem,
    *,
    supersedes_run_id: int | None = None,
    execution_mode: str = "manual",
    correction_id: int | None = None,
    restart_from_stage: str = RELEVANCE_STAGE,
    context: dict[str, Any] | None = None,
    execution_guard: PipelineExecutionGuard | None = None,
) -> ProcessingRun:
    if raw_item.normalized_item and not (
        correction_id is not None
        and raw_item.normalized_item.publication_status == "withdrawn"
    ):
        raise ValueError("raw item already has an approved normalized item")
    if restart_from_stage not in {
        RELEVANCE_STAGE,
        OCR_STAGE,
        TRANSLATION_STAGE,
        ITEM_STAGE,
    }:
        raise ValueError(f"unsupported restart stage: {restart_from_stage}")
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
        current_stage=restart_from_stage,
        execution_mode=execution_mode,
        correction_id=correction_id,
        restart_from_stage=restart_from_stage if correction_id else None,
        context=context or {},
    )
    db.add(run)
    try:
        assert_execution_owned(db, execution_guard)
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent = db.scalar(
            select(ProcessingRun).where(
                ProcessingRun.raw_item_id == raw_item.id,
                ProcessingRun.workflow_type == "item",
                ProcessingRun.status.in_(["running", "awaiting_review"]),
            )
        )
        if concurrent is None:
            raise
        return concurrent
    db.refresh(run)
    if restart_from_stage == RELEVANCE_STAGE:
        await _generate_relevance_review(
            db, run, **_guard_kwargs(execution_guard)
        )
    elif restart_from_stage == OCR_STAGE:
        await _generate_ocr_review(db, run, **_guard_kwargs(execution_guard))
    elif restart_from_stage == TRANSLATION_STAGE:
        await _generate_translation_review(
            db, run, **_guard_kwargs(execution_guard)
        )
    else:
        await _generate_item_review(db, run, **_guard_kwargs(execution_guard))
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
    elif restarted.current_stage == TRANSLATION_STAGE:
        await _generate_translation_review(db, restarted)
    else:
        raise ValueError(f"unsupported restart stage: {restarted.current_stage}")
    return restarted


async def resume_item_processing(
    db: Session,
    run: ProcessingRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
) -> ProcessingRun:
    if run.status != "running":
        return run
    pending = db.scalar(
        select(ReviewTask).where(
            ReviewTask.processing_run_id == run.id,
            ReviewTask.status == "pending",
        )
    )
    if pending is not None:
        run.status = "awaiting_review"
        assert_execution_owned(db, execution_guard)
        db.commit()
        return run
    try:
        if run.current_stage == RELEVANCE_STAGE:
            await _generate_relevance_review(
                db, run, **_guard_kwargs(execution_guard)
            )
        elif run.current_stage == OCR_STAGE:
            await _generate_ocr_review(
                db, run, **_guard_kwargs(execution_guard)
            )
        elif run.current_stage == TRANSLATION_STAGE:
            await _generate_translation_review(
                db, run, **_guard_kwargs(execution_guard)
            )
        elif run.current_stage == ITEM_STAGE:
            await _generate_item_review(
                db, run, **_guard_kwargs(execution_guard)
            )
        else:
            raise ValueError(f"unsupported resume stage: {run.current_stage}")
    except IntegrityError:
        db.rollback()
        pending = db.scalar(
            select(ReviewTask).where(
                ReviewTask.processing_run_id == run.id,
                ReviewTask.status == "pending",
            )
        )
        if pending is None:
            raise
        run = db.get(ProcessingRun, run.id)
        run.status = "awaiting_review"
        assert_execution_owned(db, execution_guard)
        db.commit()
    return run


async def approve_review(
    db: Session,
    review: ReviewTask,
    *,
    note: str | None,
    execution_guard: PipelineExecutionGuard | None = None,
) -> ProcessingRun:
    _require_pending_review(review)
    now = datetime.now(UTC)
    review.status = "approved"
    review.feedback = {"note": note} if note else {}
    review.resolved_at = now
    run = review.processing_run

    if review.stage == RELEVANCE_STAGE:
        _record_checkpoint(db, review)
        if bool(review.proposal.get("is_lol_relevant")):
            run.status = "running"
            if is_patch_preview(run.raw_item):
                run.current_stage = OCR_STAGE
                assert_execution_owned(db, execution_guard)
                db.commit()
                await _generate_ocr_review(
                    db, run, **_guard_kwargs(execution_guard)
                )
            else:
                run.current_stage = TRANSLATION_STAGE
                assert_execution_owned(db, execution_guard)
                db.commit()
                await _generate_translation_review(
                    db, run, **_guard_kwargs(execution_guard)
                )
        else:
            run.status = "completed"
            run.outcome = "irrelevant"
            run.completed_at = now
            if run.correction_id:
                correction = db.get(PipelineCorrection, run.correction_id)
                if correction is not None:
                    correction.status = "completed"
                    correction.completed_at = now
            assert_execution_owned(db, execution_guard)
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
        _record_checkpoint(
            db,
            review,
            artifact_references={"approved_media_extraction_ids": extraction_ids},
        )
        run.status = "running"
        run.current_stage = TRANSLATION_STAGE
        assert_execution_owned(db, execution_guard)
        db.commit()
        await _generate_translation_review(
            db, run, **_guard_kwargs(execution_guard)
        )
    elif review.stage == ITEM_STAGE:
        item = _apply_normalized_item(
            db,
            run.raw_item,
            review.proposal,
            processing_run_id=run.id,
        )
        _record_checkpoint(db, review, normalized_item_id=item.id)
        run.status = "completed"
        run.outcome = "approved"
        run.completed_at = now
        assert_execution_owned(db, execution_guard)
        db.commit()
        if run.correction_id:
            from app.workflows.event_aggregation import start_event_aggregation

            await start_event_aggregation(
                db,
                item,
                execution_mode=run.execution_mode,
                correction_id=run.correction_id,
                **_guard_kwargs(execution_guard),
            )
    elif review.stage == TRANSLATION_STAGE:
        run.context = {
            **run.context,
            "approved_translation_proposal": review.proposal,
        }
        _record_checkpoint(db, review)
        run.status = "running"
        run.current_stage = ITEM_STAGE
        assert_execution_owned(db, execution_guard)
        db.commit()
        await _generate_item_review(
            db, run, **_guard_kwargs(execution_guard)
        )
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
                rule_text=payload.knowledge_rule or payload.reason or "",
                correction_data=payload.corrected_values,
                source_review_id=review.id,
                lifecycle_status="draft",
                is_active=False,
            )
        )
    elif payload.feedback_type in {"translation_term", "translation_correction"}:
        if payload.reason:
            db.add(
                KnowledgeRule(
                    knowledge_type="translation",
                    scope=payload.knowledge_scope,
                    rule_text=payload.reason,
                    correction_data=payload.corrected_values,
                    source_review_id=review.id,
                    lifecycle_status="draft",
                    is_active=False,
                )
            )
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


async def _generate_relevance_review(
    db: Session,
    run: ProcessingRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
) -> None:
    raw_item = run.raw_item
    try:
        selected_rules = _knowledge_rule_snapshot(
            db, "relevance", raw_item
        )
        title = raw_item.display_title
        content = text_from_content_blocks(raw_item.content_blocks)
        source_context = _source_context(raw_item)
        knowledge_rules = _knowledge_texts_from_snapshot(selected_rules)
        assert_execution_owned(db, execution_guard)
        db.commit()
        result = await LLMClient().judge_relevance(
            title=title,
            content=content,
            source_context=source_context,
            knowledge_rules=knowledge_rules,
        )
        _replace_pending_review(
            db,
            run=run,
            stage=RELEVANCE_STAGE,
            proposal={
                **result.model_dump(mode="json"),
                "_execution_metadata": execution_metadata(result),
                "knowledge_rules": selected_rules,
            },
        )
        assert_execution_owned(db, execution_guard)
        db.commit()
    except Exception as exc:
        _mark_failed(db, run, exc, execution_guard=execution_guard)
        raise


async def _generate_ocr_review(
    db: Session,
    run: ProcessingRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
) -> None:
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
            run.current_stage = TRANSLATION_STAGE
            assert_execution_owned(db, execution_guard)
            db.commit()
            await _generate_translation_review(
                db, run, **_guard_kwargs(execution_guard)
            )
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
        assert_execution_owned(db, execution_guard)
        db.commit()
    except Exception as exc:
        _mark_failed(db, run, exc, execution_guard=execution_guard)
        raise


async def _generate_item_review(
    db: Session,
    run: ProcessingRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
) -> None:
    try:
        translation_proposal = run.context.get("approved_translation_proposal")
        if not isinstance(translation_proposal, dict):
            raise ValueError("analysis stage requires an approved translation proposal")
        rules = _knowledge_texts(db, "analysis", run.raw_item)
        knowledge_snapshot = _knowledge_rule_snapshot(
            db, "analysis", run.raw_item
        )
        assert_execution_owned(db, execution_guard)
        db.commit()
        proposal = await _build_item_proposal(
            raw_item=run.raw_item,
            translation_proposal=translation_proposal,
            rules=rules,
            knowledge_snapshot=knowledge_snapshot,
        )
        _replace_pending_review(db, run=run, stage=ITEM_STAGE, proposal=proposal)
        assert_execution_owned(db, execution_guard)
        db.commit()
    except Exception as exc:
        _mark_failed(db, run, exc, execution_guard=execution_guard)
        raise


async def _build_item_proposal(
    *,
    raw_item: RawItem,
    translation_proposal: dict[str, Any],
    rules: list[str],
    knowledge_snapshot: list[dict[str, object]] | None = None,
    ocr_corrections: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    translated_blocks = list(translation_proposal.get("translated_content_blocks") or [])
    chinese_text = text_from_content_blocks(translated_blocks)
    translated_structures = [
        value.get("translated_data")
        for value in translation_proposal.get("translated_media_extractions", [])
        if isinstance(value, dict) and isinstance(value.get("translated_data"), dict)
    ]
    analysis_content = chinese_text
    if translated_structures:
        analysis_content += "\n\n[图片版本改动结构化中文译文]\n" + json.dumps(
            translated_structures,
            ensure_ascii=False,
        )
    client = LLMClient()
    facts = await client.extract_facts(
        title=str(translation_proposal.get("translated_title") or ""),
        content=analysis_content,
        source_context=_source_context(raw_item),
        knowledge_rules=rules,
    )
    importance = await client.score_importance(
        content=analysis_content,
        extracted_facts=facts.model_dump(mode="json"),
    )
    authority = _source_authority(raw_item)
    primary_topic = topic_from_category(facts.category)
    importance_dimensions = {
        name: getattr(importance, name).model_dump(mode="json") for name in DIMENSIONS
    }
    importance_score, importance_calculation = calculate_importance(
        importance_dimensions,
        primary_topic=primary_topic,
    )
    credibility_score = max(0.0, min(authority, 100) / 100)
    is_official = authority >= 100
    return {
        **translation_proposal,
        "normalized_title": facts.title,
        "summary": facts.summary,
        "category": facts.category,
        "entities": normalize_entities(
            [entity.model_dump(mode="json") for entity in facts.entities]
        ),
        "primary_topic": primary_topic,
        "secondary_topics": [],
        "facets": {
            "product_scope": "lol_pc",
            "region": "unknown",
            "information_stage": "announcement",
        },
        "ontology_version": ONTOLOGY_VERSION,
        "importance_score": importance_score,
        "importance_evidence": [
            str(importance_dimensions[name]["evidence"]) for name in DIMENSIONS
        ],
        "importance_dimensions": importance_dimensions,
        "importance_policy_version": IMPORTANCE_POLICY_VERSION,
        "importance_calculation": importance_calculation,
        "credibility": "official" if is_official else "unverified",
        "credibility_score": credibility_score,
        "credibility_evidence": [
            (
                f"信源“{raw_item.source.name}”被配置为官方来源（权威度 {authority}）"
                if is_official
                else f"信源“{raw_item.source.name}”的配置权威度为 {authority}"
            )
        ],
        "credibility_components": {
            "source_prior": credibility_score,
            "source_role": "first_party" if is_official else "third_party",
            "is_repost": has_quoted_post(raw_item.content_blocks),
            "evidence_quality": "direct_text",
            "ocr_confidence": None,
            "translation_confidence": None,
        },
        "credibility_policy_version": "credibility-v1-components",
        "language": raw_item.language,
        "analysis_model": settings.model_name,
        "analysis_version": "v5-importance-rubric",
        "_execution_metadata": {
            "fact_extraction": execution_metadata(facts),
            "importance_scoring": execution_metadata(importance),
        },
        "knowledge_rules": knowledge_snapshot or [],
        "ocr_corrections": ocr_corrections or [],
    }


async def _generate_translation_review(
    db: Session,
    run: ProcessingRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
) -> None:
    try:
        extraction_ids = _extraction_ids(run.context)
        media_extractions = list(
            db.scalars(
                select(MediaExtraction)
                .where(MediaExtraction.id.in_(extraction_ids))
                .order_by(MediaExtraction.id)
            )
        )
        glossary = _glossary_payload(
            db, text_from_content_blocks(run.raw_item.content_blocks)
        )
        selected_rules = _knowledge_rule_snapshot(
            db, "translation", run.raw_item
        )
        assert_execution_owned(db, execution_guard)
        db.commit()
        translation = await build_translation(
            run.raw_item,
            media_extractions=media_extractions,
            glossary=glossary,
            rules=_knowledge_texts_from_snapshot(selected_rules),
        )
        proposal = {
            "normalized_text": text_from_content_blocks(run.raw_item.content_blocks),
            "language": run.raw_item.language,
            "source_language": translation.source_language,
            "target_language": translation.target_language,
            "translated_title": translation.translated_title,
            "translated_text": translation.translated_text,
            "translated_content_blocks": translation.translated_content_blocks,
            "translation_status": translation.translation_status,
            "translation_model": translation.translation_model,
            "approved_media_extraction_ids": extraction_ids,
            "media_extractions": extraction_context(media_extractions),
            "translated_media_extractions": translation.translated_media_extractions,
            "glossary_term_ids": [
                int(term["id"])
                for term in glossary
                if isinstance(term.get("id"), int)
            ],
            "knowledge_rules": selected_rules,
        }
        if translation.translation_status == "not_required":
            run.context = {
                **run.context,
                "approved_translation_proposal": proposal,
            }
            run.status = "running"
            run.current_stage = ITEM_STAGE
            assert_execution_owned(db, execution_guard)
            db.commit()
            await _generate_item_review(
                db, run, **_guard_kwargs(execution_guard)
            )
            return
        _replace_pending_review(
            db,
            run=run,
            stage=TRANSLATION_STAGE,
            proposal=proposal,
        )
        assert_execution_owned(db, execution_guard)
        db.commit()
    except Exception as exc:
        _mark_failed(db, run, exc, execution_guard=execution_guard)
        raise


def _apply_normalized_item(
    db: Session,
    raw_item: RawItem,
    proposal: dict[str, Any],
    processing_run_id: int | None = None,
) -> NormalizedItem:
    allowed_fields = {
        "normalized_title",
        "normalized_text",
        "summary",
        "category",
        "entities",
        "primary_topic",
        "secondary_topics",
        "facets",
        "ontology_version",
        "importance_score",
        "importance_dimensions",
        "importance_policy_version",
        "importance_calculation",
        "credibility",
        "credibility_score",
        "credibility_evidence",
        "credibility_components",
        "credibility_policy_version",
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
                _normalize_entities(value)
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
    db.add(
        NormalizedItemRevision(
            normalized_item_id=item.id,
            revision=item.current_revision,
            snapshot={
                **values,
                "approved_media_extraction_ids": _extraction_ids(proposal),
                "translated_media_extractions": proposal.get(
                    "translated_media_extractions", []
                ),
            },
            processing_run_id=processing_run_id,
            change_note=(
                "corrected and republished"
                if item.current_revision > 1
                else "initial publication"
            ),
        )
    )
    db.flush()
    extract_traceable_claim(db, item)
    return item


def _record_checkpoint(
    db: Session,
    review: ReviewTask,
    *,
    normalized_item_id: int | None = None,
    artifact_references: dict[str, Any] | None = None,
) -> ProcessingCheckpoint:
    run = review.processing_run
    checkpoint = ProcessingCheckpoint(
        raw_item_id=run.raw_item_id,
        normalized_item_id=normalized_item_id,
        processing_run_id=run.id,
        correction_id=run.correction_id,
        stage=review.stage,
        output_snapshot=dict(review.proposal),
        artifact_references={
            **(artifact_references or {}),
            "workflow_version": "reviewed-pipeline-v2",
            "policy_version": review.policy_version,
            "execution_metadata": review.proposal.get(
                "_execution_metadata", {}
            ),
        },
        knowledge_snapshot={
            "knowledge_rules": review.proposal.get("knowledge_rules", []),
            "glossary_term_ids": review.proposal.get("glossary_term_ids", []),
        },
        model_name=(
            review.proposal.get("analysis_model")
            or review.proposal.get("translation_model")
            or review.proposal.get("model")
        ),
        decision_source=review.decision_source,
    )
    db.add(checkpoint)
    return checkpoint


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
        ITEM_STAGE: {"analysis_correction"},
        TRANSLATION_STAGE: {"translation_term", "translation_correction"},
    }
    if payload.feedback_type not in allowed.get(stage, set()):
        raise ValueError(
            f"feedback_type={payload.feedback_type} is invalid for stage={stage}"
        )
    if (
        payload.feedback_type in {"translation_term", "translation_correction"}
        and not payload.reason
        and not payload.glossary_updates
    ):
        raise ValueError("translation rejection requires a reason or glossary update")


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
    return _knowledge_texts_from_snapshot(
        _knowledge_rule_snapshot(db, knowledge_type, raw_item)
    )


def _knowledge_rule_snapshot(
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
            KnowledgeRule.is_active.is_(True),
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


def _knowledge_texts_from_snapshot(
    rules: list[dict[str, object]],
) -> list[str]:
    return [
        f"[{rule['scope']} v{rule['version']}] {rule['rule_text']}"
        for rule in rules
    ]


def _glossary_payload(db: Session, source_text: str = "") -> list[dict[str, object]]:
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
        return 60
    return 50


def _require_pending_review(review: ReviewTask) -> None:
    if review.status != "pending":
        raise ValueError(f"review task cannot be resolved from status={review.status}")


def _mark_failed(
    db: Session,
    run: ProcessingRun,
    exc: Exception,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
) -> None:
    db.rollback()
    failed = db.get(ProcessingRun, run.id)
    if failed:
        failed.status = "failed"
        failed.outcome = "system_error"
        failed.error_message = str(exc)[:4000]
        failed.completed_at = datetime.now(UTC)
        assert_execution_owned(db, execution_guard)
        db.commit()
