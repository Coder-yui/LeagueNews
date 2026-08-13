import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.content_blocks import has_repost_evidence, text_from_content_blocks
from app.core.config import settings
from app.domain.importance import (
    IMPORTANCE_POLICY_VERSION,
    calculate_importance,
    calculate_message_priority,
    derive_importance_profile,
    normalize_importance_features,
)
from app.domain.evidence import evaluate_evidence_gate
from app.domain.message_entities import normalize_entities
from app.domain.message_taxonomy import CLASSIFICATION_VERSION
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
from app.services.classification_source import resolve_classification_source
from app.services.llm import LLMClient, execution_metadata
from app.services.media_publication import publish_raw_item_media
from app.services.pipeline_execution import (
    PipelineExecutionGuard,
    assert_execution_owned,
)
from app.services.raw_item_versions import is_latest_raw_item
from app.workflows.translate_item import build_translation
from app.workflows.understand_media import (
    extraction_context,
    is_patch_preview,
    structure_patch_extraction,
    understand_patch_media,
)

RELEVANCE_STAGE = "relevance"
OCR_STAGE = "image_ocr"
TRANSLATION_STAGE = "translation"
MESSAGE_ANALYSIS_STAGE = "message_analysis"
IMPORTANCE_STAGE = "importance"
MESSAGE_STAGES = frozenset(
    {
        RELEVANCE_STAGE,
        OCR_STAGE,
        TRANSLATION_STAGE,
        MESSAGE_ANALYSIS_STAGE,
        IMPORTANCE_STAGE,
    }
)


def _guard_kwargs(
    execution_guard: PipelineExecutionGuard | None,
) -> dict[str, PipelineExecutionGuard]:
    return {"execution_guard": execution_guard} if execution_guard is not None else {}


async def _generate_current_stage_review(
    db: Session,
    run: ProcessingRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
) -> None:
    generators = {
        RELEVANCE_STAGE: _evaluate_relevance,
        OCR_STAGE: _generate_ocr_review,
        TRANSLATION_STAGE: _generate_translation_review,
        MESSAGE_ANALYSIS_STAGE: _generate_message_analysis_review,
        IMPORTANCE_STAGE: _generate_importance_review,
    }
    try:
        generator = generators[run.current_stage]
    except KeyError as exc:
        raise ValueError(f"unsupported item stage: {run.current_stage}") from exc
    await generator(db, run, **_guard_kwargs(execution_guard))


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
    if not is_latest_raw_item(db, raw_item):
        raise ValueError("raw item has been superseded by a newer revision")
    if raw_item.normalized_item and raw_item.normalized_item.publication_status != "withdrawn":
        raise ValueError("raw item already has an approved normalized item")
    if restart_from_stage not in MESSAGE_STAGES:
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
    await _generate_current_stage_review(db, run, execution_guard=execution_guard)
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

    await _generate_current_stage_review(db, restarted)
    return restarted


async def resume_item_processing(
    db: Session,
    run: ProcessingRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
) -> ProcessingRun:
    if run.status != "running":
        return run
    if not is_latest_raw_item(db, run.raw_item):
        raise ValueError("raw item has been superseded by a newer revision")
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
        await _generate_current_stage_review(db, run, execution_guard=execution_guard)
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
    run = review.processing_run
    if not is_latest_raw_item(db, run.raw_item):
        raise ValueError("raw item has been superseded by a newer revision")
    now = datetime.now(UTC)
    review.status = "approved"
    review.feedback = {"note": note} if note else {}
    review.resolved_at = now
    if review.stage == RELEVANCE_STAGE:
        _record_checkpoint(db, review)
        if _should_continue_relevance(review.proposal):
            run.status = "running"
            if is_patch_preview(run.raw_item):
                run.current_stage = OCR_STAGE
                assert_execution_owned(db, execution_guard)
                db.commit()
                await _generate_ocr_review(db, run, **_guard_kwargs(execution_guard))
            else:
                run.current_stage = TRANSLATION_STAGE
                assert_execution_owned(db, execution_guard)
                db.commit()
                await _generate_translation_review(db, run, **_guard_kwargs(execution_guard))
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
            "evidence_gate": evaluate_evidence_gate(
                run.raw_item,
                designer_patch_images=True,
                designer_patch_extraction_count=len(extraction_ids),
            ).as_dict(),
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
        await _generate_translation_review(db, run, **_guard_kwargs(execution_guard))
    elif review.stage == MESSAGE_ANALYSIS_STAGE:
        review.proposal = {
            **review.proposal,
            "classification_source": resolve_classification_source(
                db,
                run.raw_item,
                content_form=str(review.proposal.get("content_form") or "original"),
            ),
        }
        run.context = {
            **run.context,
            "approved_message_analysis_proposal": review.proposal,
        }
        if review.proposal.get("content_form") in {"media_only", "link_only"}:
            item_proposal = _build_item_proposal(
                raw_item=run.raw_item,
                translation_proposal=run.context["approved_translation_proposal"],
                analysis_proposal=review.proposal,
                importance_proposal=None,
                relevance_proposal=run.context.get("relevance_decision"),
                evidence_gate=run.context.get("evidence_gate"),
                knowledge_snapshot=list(review.proposal.get("knowledge_rules") or []),
            )
            await _publish_approved_item(
                db,
                review,
                proposal=item_proposal,
                completed_at=now,
                execution_guard=execution_guard,
            )
        else:
            _record_checkpoint(db, review)
            run.status = "running"
            run.current_stage = IMPORTANCE_STAGE
            assert_execution_owned(db, execution_guard)
            db.commit()
            await _generate_importance_review(db, run, **_guard_kwargs(execution_guard))
    elif review.stage == IMPORTANCE_STAGE:
        run.context = {
            **run.context,
            "approved_importance_proposal": review.proposal,
        }
        translation_proposal = run.context.get("approved_translation_proposal")
        analysis_proposal = run.context.get("approved_message_analysis_proposal")
        if not all(
            isinstance(value, dict)
            for value in (
                translation_proposal,
                analysis_proposal,
            )
        ):
            raise ValueError("importance approval is missing an approved upstream proposal")
        item_proposal = _build_item_proposal(
            raw_item=run.raw_item,
            translation_proposal=translation_proposal,
            analysis_proposal=analysis_proposal,
            importance_proposal=review.proposal,
            relevance_proposal=run.context.get("relevance_decision"),
            evidence_gate=run.context.get("evidence_gate"),
            knowledge_snapshot=list(analysis_proposal.get("knowledge_rules") or []),
        )
        await _publish_approved_item(
            db,
            review,
            proposal=item_proposal,
            completed_at=now,
            execution_guard=execution_guard,
        )
    elif review.stage == TRANSLATION_STAGE:
        run.context = {
            **run.context,
            "approved_translation_proposal": review.proposal,
        }
        _record_checkpoint(db, review)
        run.status = "running"
        run.current_stage = MESSAGE_ANALYSIS_STAGE
        assert_execution_owned(db, execution_guard)
        db.commit()
        await _generate_message_analysis_review(db, run, **_guard_kwargs(execution_guard))
    else:
        raise ValueError(f"unsupported review stage: {review.stage}")
    db.refresh(run)
    return run


async def _publish_approved_item(
    db: Session,
    review: ReviewTask,
    *,
    proposal: dict[str, Any],
    completed_at: datetime,
    execution_guard: PipelineExecutionGuard | None,
) -> None:
    run = review.processing_run
    item = _apply_normalized_item(
        db,
        run.raw_item,
        proposal,
        processing_run_id=run.id,
    )
    _record_checkpoint(db, review, normalized_item_id=item.id)
    run.status = "completed"
    run.outcome = "approved"
    run.completed_at = completed_at
    assert_execution_owned(db, execution_guard)
    db.commit()


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

    if payload.feedback_type == "analysis_correction" and review.stage != RELEVANCE_STAGE:
        db.add(
            KnowledgeRule(
                knowledge_type="analysis",
                scope=payload.knowledge_scope,
                rule_text=payload.knowledge_rule or payload.reason or "",
                correction_data=payload.corrected_values,
                source_review_id=review.id,
                lifecycle_status="draft",
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


async def _evaluate_relevance(
    db: Session,
    run: ProcessingRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
) -> None:
    raw_item = run.raw_item
    try:
        title = raw_item.display_title
        content = text_from_content_blocks(raw_item.content_blocks)
        evidence_gate = evaluate_evidence_gate(
            raw_item,
            designer_patch_images=is_patch_preview(raw_item),
        ).as_dict()
        run.context = {**run.context, "evidence_gate": evidence_gate}
        source_context = {
            **_source_context(raw_item),
            "evidence_gate": evidence_gate,
        }
        assert_execution_owned(db, execution_guard)
        db.commit()
        result = await LLMClient().judge_relevance(
            title=title,
            content=content,
            source_context=source_context,
        )
        proposal = {
            **result.model_dump(mode="json"),
            "_execution_metadata": execution_metadata(result),
        }
        run.context = {
            **run.context,
            "relevance_decision": proposal,
        }
        _record_automatic_checkpoint(
            db,
            run=run,
            stage=RELEVANCE_STAGE,
            proposal=proposal,
            policy_version="ai-direct-v1",
        )
        should_continue = _should_continue_relevance(proposal)
        if should_continue:
            run.status = "running"
            run.current_stage = OCR_STAGE if is_patch_preview(raw_item) else TRANSLATION_STAGE
        else:
            now = datetime.now(UTC)
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
        if should_continue:
            if run.current_stage == OCR_STAGE:
                await _generate_ocr_review(db, run, **_guard_kwargs(execution_guard))
            else:
                await _generate_translation_review(db, run, **_guard_kwargs(execution_guard))
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
            await _generate_translation_review(db, run, **_guard_kwargs(execution_guard))
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


def _analysis_content(translation_proposal: dict[str, Any]) -> str:
    translated_blocks = list(translation_proposal.get("translated_content_blocks") or [])
    content = text_from_content_blocks(translated_blocks)
    translated_structures = [
        value.get("translated_data")
        for value in translation_proposal.get("translated_media_extractions", [])
        if isinstance(value, dict) and isinstance(value.get("translated_data"), dict)
    ]
    if translated_structures:
        content += "\n\n[图片版本改动结构化中文译文]\n" + json.dumps(
            translated_structures,
            ensure_ascii=False,
        )
    return content


def _message_analysis_content(translation_proposal: dict[str, Any]) -> str:
    title = str(translation_proposal.get("translated_title") or "").strip()
    body = _analysis_content(translation_proposal).strip()
    sections = []
    if title:
        sections.append(f"[消息标题]\n{title}")
    if body:
        sections.append(f"[消息正文]\n{body}")
    return "\n\n".join(sections)


def _importance_scoring_content(
    translation_proposal: dict[str, Any],
    fact_proposal: dict[str, Any],
) -> str:
    return "\n".join(
        value
        for value in (
            str(fact_proposal.get("title") or "").strip(),
            _analysis_content(translation_proposal).strip(),
        )
        if value
    )


async def _generate_message_analysis_review(
    db: Session,
    run: ProcessingRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
) -> None:
    try:
        translation_proposal = run.context.get("approved_translation_proposal")
        if not isinstance(translation_proposal, dict):
            raise ValueError("message analysis stage requires an approved translation proposal")
        rules = _knowledge_texts(db, "analysis", run.raw_item)
        knowledge_snapshot = _knowledge_rule_snapshot(db, "analysis", run.raw_item)
        assert_execution_owned(db, execution_guard)
        db.commit()
        content_blocks = list(translation_proposal.get("translated_content_blocks") or [])
        analysis = await LLMClient().analyze_message_content(
            title=str(translation_proposal.get("translated_title") or ""),
            content=_message_analysis_content(translation_proposal),
            evidence_structure={
                "content_block_types": [
                    str(block.get("type") or "")
                    for block in run.raw_item.content_blocks
                    if isinstance(block, dict)
                ],
                "translated_block_types": [
                    str(block.get("type") or "")
                    for block in content_blocks
                    if isinstance(block, dict)
                ],
                "canonical_url": run.raw_item.canonical_url,
                "has_repost_evidence": has_repost_evidence(run.raw_item.content_blocks),
            },
            source_context=_analysis_source_context(run),
            knowledge_rules=rules,
        )
        proposal = {
            **analysis.model_dump(mode="json"),
            "classification_source": resolve_classification_source(
                db,
                run.raw_item,
                content_form=analysis.content_form,
            ),
            "analysis_model": settings.model_name,
            "_execution_metadata": {
                "message_analysis": execution_metadata(analysis),
            },
            "knowledge_rules": knowledge_snapshot,
        }
        _replace_pending_review(db, run=run, stage=MESSAGE_ANALYSIS_STAGE, proposal=proposal)
        assert_execution_owned(db, execution_guard)
        db.commit()
    except Exception as exc:
        _mark_failed(db, run, exc, execution_guard=execution_guard)
        raise


async def _generate_importance_review(
    db: Session,
    run: ProcessingRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
) -> None:
    try:
        translation = run.context.get("approved_translation_proposal")
        analysis = run.context.get("approved_message_analysis_proposal")
        if not isinstance(translation, dict):
            raise ValueError("importance stage requires an approved translation proposal")
        if not isinstance(analysis, dict):
            raise ValueError("importance stage requires an approved message analysis")
        content = _analysis_content(translation)
        scoring_content = _importance_scoring_content(translation, analysis)
        products = list(analysis.get("products") or ["unknown"])
        content_form = str(analysis.get("content_form") or "original")
        classification_source = dict(analysis.get("classification_source") or {})
        if not classification_source:
            classification_source = resolve_classification_source(
                db,
                run.raw_item,
                content_form=content_form,
            )
            analysis = {
                **analysis,
                "classification_source": classification_source,
                "classification_version": CLASSIFICATION_VERSION,
            }
            run.context = {
                **run.context,
                "approved_message_analysis_proposal": analysis,
            }
        assert_execution_owned(db, execution_guard)
        db.commit()
        client = LLMClient()
        classification_importance = await client.classify_and_score_importance(
            content=content,
            extracted_facts={
                key: analysis[key]
                for key in ("title", "summary", "entities", "products", "content_form")
                if key in analysis
            },
            products=products,
            content_form=content_form,
            source_context={
                **_analysis_source_context(run),
                "classification_source": classification_source,
                "classification_source_kind": str(
                    classification_source.get("source_kind") or "unknown"
                ),
            },
            knowledge_rules=_knowledge_texts_from_snapshot(
                list(analysis.get("knowledge_rules") or [])
            ),
        )
        result = classification_importance.model_dump(mode="json")
        message_type = str(result.pop("message_type"))
        topics = list(result.pop("topics"))
        importance_profile = derive_importance_profile(
            message_type=message_type,
            topics=topics,
            content=scoring_content,
        )
        importance_features = normalize_importance_features(
            result,
            profile=importance_profile,
            content=scoring_content,
        )
        score, calculation = calculate_importance(
            importance_features,
            message_type=message_type,
            topics=topics,
            content_form=content_form,
            content=scoring_content,
        )
        priority_score, priority_calculation = calculate_message_priority(
            score,
            content_form=content_form,
            audience_region=str(importance_features["audience_region"]),
        )
        evidence = list(importance_features["evidence"])
        dimensions = {
            "importance_profile": {
                "value": calculation["importance_profile"],
                "evidence": evidence[0],
            },
            "scale": {
                "value": importance_features["scale"],
                "evidence": next(iter(evidence[1:]), evidence[0]),
            },
            "audience_region": {
                "value": importance_features["audience_region"],
                "evidence": next(iter(evidence[2:]), evidence[-1]),
            },
            "competition_region": {
                "value": importance_features["competition_region"],
                "evidence": next(iter(evidence[3:]), evidence[-1]),
            },
            "prominence": {
                "value": importance_features["prominence"],
                "evidence": next(iter(evidence[4:]), evidence[-1]),
            },
            "skin_tier": {
                "value": importance_features["skin_tier"],
                "evidence": next(iter(evidence[5:]), evidence[-1]),
            },
        }
        proposal = {
            "message_type": message_type,
            "topics": topics,
            "classification_source": classification_source,
            "importance_score": score,
            "importance_evidence": evidence,
            "importance_dimensions": dimensions,
            "importance_policy_version": IMPORTANCE_POLICY_VERSION,
            "importance_calculation": calculation,
            "priority_score": priority_score,
            "priority_calculation": priority_calculation,
            "analysis_model": settings.model_name,
            "_execution_metadata": {
                "classification_importance": execution_metadata(classification_importance),
            },
        }
        _replace_pending_review(
            db,
            run=run,
            stage=IMPORTANCE_STAGE,
            proposal=proposal,
        )
        assert_execution_owned(db, execution_guard)
        db.commit()
    except Exception as exc:
        _mark_failed(db, run, exc, execution_guard=execution_guard)
        raise


def _build_item_proposal(
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
        "normalized_title": _normalized_title(
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
        "_execution_metadata": {
            **dict(classification.get("_execution_metadata") or {}),
            **dict(importance.get("_execution_metadata") or {}),
        },
        "knowledge_rules": knowledge_snapshot or [],
        "ocr_corrections": ocr_corrections or [],
    }


def _normalized_title(
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
        glossary = _glossary_payload(db, text_from_content_blocks(run.raw_item.content_blocks))
        selected_rules = _knowledge_rule_snapshot(db, "translation", run.raw_item)
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
                int(term["id"]) for term in glossary if isinstance(term.get("id"), int)
            ],
            "knowledge_rules": selected_rules,
        }
        if translation.translation_status == "not_required":
            run.context = {
                **run.context,
                "approved_translation_proposal": proposal,
            }
            run.status = "running"
            run.current_stage = MESSAGE_ANALYSIS_STAGE
            assert_execution_owned(db, execution_guard)
            db.commit()
            await _generate_message_analysis_review(db, run, **_guard_kwargs(execution_guard))
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
            _normalize_entities(value) if key == "entities" and isinstance(value, list) else value
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
            "execution_metadata": review.proposal.get("_execution_metadata", {}),
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


def _record_automatic_checkpoint(
    db: Session,
    *,
    run: ProcessingRun,
    stage: str,
    proposal: dict[str, Any],
    policy_version: str,
) -> ProcessingCheckpoint:
    metadata = dict(proposal.get("_execution_metadata") or {})
    checkpoint = ProcessingCheckpoint(
        raw_item_id=run.raw_item_id,
        processing_run_id=run.id,
        correction_id=run.correction_id,
        stage=stage,
        output_snapshot=dict(proposal),
        artifact_references={
            "workflow_version": "reviewed-pipeline-v2",
            "policy_version": policy_version,
            "execution_metadata": metadata,
        },
        knowledge_snapshot={},
        model_name=(proposal.get("model") or metadata.get("model") or metadata.get("model_name")),
        decision_source="automatic",
    )
    db.add(checkpoint)
    return checkpoint


def _should_continue_relevance(proposal: dict[str, Any]) -> bool:
    return proposal.get("decision") != "irrelevant"


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
        RELEVANCE_STAGE: {"analysis_correction"},
        OCR_STAGE: {"ocr_error"},
        MESSAGE_ANALYSIS_STAGE: {"analysis_correction"},
        IMPORTANCE_STAGE: {"analysis_correction"},
        TRANSLATION_STAGE: {"translation_term", "translation_correction"},
    }
    if payload.feedback_type not in allowed.get(stage, set()):
        raise ValueError(f"feedback_type={payload.feedback_type} is invalid for stage={stage}")
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
        role = str(value.get("role") or "context").casefold()
        record["role"] = role if role in {"core", "context", "affected"} else "context"
        normalized.append(record)
    return normalized


def _knowledge_texts(db: Session, knowledge_type: str, raw_item: RawItem) -> list[str]:
    return _knowledge_texts_from_snapshot(_knowledge_rule_snapshot(db, knowledge_type, raw_item))


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


def _knowledge_texts_from_snapshot(
    rules: list[dict[str, object]],
) -> list[str]:
    return [f"[{rule['scope']} v{rule['version']}] {rule['rule_text']}" for rule in rules]


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
        "is_repost": has_repost_evidence(raw_item.content_blocks),
    }


def _analysis_source_context(run: ProcessingRun) -> dict[str, object]:
    return {
        **_source_context(run.raw_item),
        "evidence_gate": dict(run.context.get("evidence_gate") or {}),
    }


def _source_authority(raw_item: RawItem) -> int:
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
