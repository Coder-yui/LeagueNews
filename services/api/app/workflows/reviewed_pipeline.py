import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.content_blocks import has_repost_evidence, text_from_content_blocks
from app.core.config import settings
from app.domain.content_semantics import (
    GAMEPLAY_CHANGE_SUBTOPICS,
    OFFICIAL_ONLY_UPDATE_SUBTOPICS,
    has_hotfix_signal,
    has_test_environment_signal,
    is_catalog_asset_preview,
)
from app.domain.importance import (
    IMPORTANCE_POLICY_VERSION,
    calculate_importance,
    calculate_message_priority,
    normalize_importance_analysis,
)
from app.domain.evidence import evaluate_evidence_gate
from app.domain.ontology import (
    ONTOLOGY_VERSION,
    normalize_entities,
    normalize_event_mentions,
)
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
from app.services.claims import persist_generated_claims
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
FACT_CLASSIFY_STAGE = "fact_classify"
IMPORTANCE_STAGE = "importance"
CLAIM_STAGE = "claim_gen"
MESSAGE_STAGES = frozenset(
    {
        RELEVANCE_STAGE,
        OCR_STAGE,
        TRANSLATION_STAGE,
        FACT_CLASSIFY_STAGE,
        IMPORTANCE_STAGE,
        CLAIM_STAGE,
    }
)
_EVENT_PASS_EVIDENCE = re.compile(
    r"通行证|宝典|战令|\b(?:battle\s*)?pass\b|购买等级|等级奖励|里程碑"
    r"|付费解锁.{0,12}(?:等级|里程碑|进阶奖励)",
    re.IGNORECASE,
)
_LOTTERY_REWARD_EVIDENCE = re.compile(
    r"抽奖|抽取|概率|几率|有机会|开奖|最高(?:可)?(?:获得|领取|领到|得到)",
    re.IGNORECASE,
)
_ACQUISITION_METHOD_EVIDENCE = re.compile(
    r"获取方式|获得方式|如何(?:获取|获得)|怎么(?:获取|获得)|获取途径|奖励途径",
    re.IGNORECASE,
)
_REWARD_CLAIM_EVIDENCE = re.compile(
    r"(?:皮肤|奖励|宝箱).{0,12}(?:领取|兑换|开箱)"
    r"|(?:领取|兑换|开箱).{0,12}(?:皮肤|奖励|宝箱)",
    re.IGNORECASE,
)
_REWARD_CLAIM_OPEN_EVIDENCE = re.compile(
    r"(?:现已|已经|开放|开启|开始|可领|可领取).{0,16}(?:领取|兑换|开箱)"
    r"|(?:领取|兑换|开箱).{0,16}(?:现已|已经|开放|开启|开始|可用)",
    re.IGNORECASE,
)
_PAID_REWARD_EVIDENCE = re.compile(
    r"付费|点券|购买|充值|氪金|付钱|花钱",
    re.IGNORECASE,
)
_RELEASE_ACTION_SUFFIX = re.compile(
    r"(?:即将|正式|现已|同步)?(?:上线|发布|登场|推出|开售).*$",
    re.IGNORECASE,
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
        FACT_CLASSIFY_STAGE: _generate_fact_review,
        IMPORTANCE_STAGE: _generate_importance_review,
        CLAIM_STAGE: _generate_claim_review,
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
    elif review.stage == FACT_CLASSIFY_STAGE:
        run.context = {
            **run.context,
            "approved_fact_proposal": review.proposal,
            "approved_classification_proposal": review.proposal,
        }
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
        _record_checkpoint(db, review)
        run.status = "running"
        run.current_stage = CLAIM_STAGE
        assert_execution_owned(db, execution_guard)
        db.commit()
        await _generate_claim_review(db, run, **_guard_kwargs(execution_guard))
    elif review.stage == CLAIM_STAGE:
        run.context = {
            **run.context,
            "approved_claim_proposal": review.proposal,
        }
        translation_proposal = run.context.get("approved_translation_proposal")
        fact_proposal = run.context.get("approved_fact_proposal")
        classification_proposal = run.context.get("approved_classification_proposal")
        importance_proposal = run.context.get("approved_importance_proposal")
        if not all(
            isinstance(value, dict)
            for value in (
                translation_proposal,
                fact_proposal,
                classification_proposal,
                importance_proposal,
            )
        ):
            raise ValueError("claim approval is missing an approved upstream proposal")
        item_proposal = _build_item_proposal(
            raw_item=run.raw_item,
            translation_proposal=translation_proposal,
            fact_proposal=fact_proposal,
            classification_proposal=classification_proposal,
            importance_proposal=importance_proposal,
            claim_proposal=review.proposal,
            relevance_proposal=run.context.get("relevance_decision"),
            evidence_gate=run.context.get("evidence_gate"),
            knowledge_snapshot=list(fact_proposal.get("knowledge_rules") or []),
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
        run.current_stage = FACT_CLASSIFY_STAGE
        assert_execution_owned(db, execution_guard)
        db.commit()
        await _generate_fact_review(db, run, **_guard_kwargs(execution_guard))
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

    from app.workflows.event_aggregation import start_event_aggregation

    await start_event_aggregation(
        db,
        item,
        execution_mode=run.execution_mode,
        correction_id=run.correction_id,
        **_guard_kwargs(execution_guard),
    )


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
        if evidence_gate["decision"] == "insufficient_evidence":
            proposal = {
                "product_scope": "uncertain",
                "is_lol_relevant": False,
                "confidence": 0.0,
                "reason": evidence_gate["reason"],
                "evidence_gate": evidence_gate,
                "requires_manual_review": False,
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
                policy_version="evidence-insufficient-v1",
            )
            now = datetime.now(UTC)
            for review in run.reviews:
                if review.status == "pending":
                    review.status = "superseded"
                    review.resolved_at = now
            run.status = "completed"
            run.outcome = "insufficient_evidence"
            run.completed_at = now
            if run.correction_id:
                correction = db.get(PipelineCorrection, run.correction_id)
                if correction is not None:
                    correction.status = "completed"
                    correction.completed_at = now
            assert_execution_owned(db, execution_guard)
            db.commit()
            return
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


async def _generate_fact_review(
    db: Session,
    run: ProcessingRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
) -> None:
    try:
        translation_proposal = run.context.get("approved_translation_proposal")
        if not isinstance(translation_proposal, dict):
            raise ValueError("fact extraction stage requires an approved translation proposal")
        rules = _knowledge_texts(db, "analysis", run.raw_item)
        knowledge_snapshot = _knowledge_rule_snapshot(db, "analysis", run.raw_item)
        assert_execution_owned(db, execution_guard)
        db.commit()
        client = LLMClient()
        facts = await client.extract_facts(
            title=str(translation_proposal.get("translated_title") or ""),
            content=_analysis_content(translation_proposal),
            source_context=_analysis_source_context(run),
            knowledge_rules=rules,
        )
        proposal = {
            **facts.model_dump(mode="json"),
            "analysis_model": settings.model_name,
            "_execution_metadata": {
                "fact_extraction": execution_metadata(facts),
            },
            "knowledge_rules": knowledge_snapshot,
        }
        classification = await client.classify(
            content=_analysis_content(translation_proposal),
            extracted_facts={
                key: proposal[key] for key in ("title", "summary", "entities") if key in proposal
            },
            source_context=_analysis_source_context(run),
        )
        proposal = {
            **proposal,
            **classification.model_dump(mode="json"),
            "_execution_metadata": {
                **dict(proposal.get("_execution_metadata") or {}),
                "classification": execution_metadata(classification),
            },
        }
        proposal = _apply_classification_evidence_guardrails(proposal, run.raw_item)
        _replace_pending_review(db, run=run, stage=FACT_CLASSIFY_STAGE, proposal=proposal)
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
        facts = run.context.get("approved_fact_proposal")
        classification = run.context.get("approved_classification_proposal")
        if not isinstance(translation, dict):
            raise ValueError("importance stage requires an approved translation proposal")
        if not isinstance(facts, dict):
            raise ValueError("importance stage requires an approved fact proposal")
        if not isinstance(classification, dict):
            raise ValueError("importance stage requires an approved classification proposal")
        content = _analysis_content(translation)
        scoring_content = _importance_scoring_content(translation, facts)
        assert_execution_owned(db, execution_guard)
        db.commit()
        client = LLMClient()
        importance = await client.score_importance(
            content=content,
            extracted_facts={
                key: facts[key] for key in ("title", "summary", "entities") if key in facts
            },
            classification={
                key: classification[key]
                for key in (
                    "source_kind",
                    "information_stage",
                    "content_form",
                    "topic",
                    "subtopic",
                    "secondary_topics",
                    "event_assertion",
                    "temporal",
                )
                if key in classification
            },
            source_context=_analysis_source_context(run),
        )
        editorial_analysis = importance.model_dump(mode="json")
        editorial_analysis = normalize_importance_analysis(
            editorial_analysis,
            primary_topic=str(classification["topic"]),
            subtopic=str(classification["subtopic"]),
            content=scoring_content,
            source_kind=str(classification["source_kind"]),
        )
        score, calculation = calculate_importance(
            editorial_analysis,
            primary_topic=str(classification["topic"]),
            subtopic=str(classification["subtopic"]),
            content=scoring_content,
            source_kind=str(classification["source_kind"]),
        )
        priority_score, priority_calculation = calculate_message_priority(
            score,
            information_stage=str(classification["information_stage"]),
            content_form=str(classification["content_form"]),
            audience_region=str(editorial_analysis["audience_region"]),
        )
        evidence = list(editorial_analysis["evidence"])
        dimensions = {
            "editorial_subtype": {
                "value": calculation["editorial_subtype"],
                "evidence": evidence[0],
            },
            "scale": {
                "value": editorial_analysis["scale"],
                "evidence": next(iter(evidence[1:]), evidence[0]),
            },
            "audience_region": {
                "value": editorial_analysis["audience_region"],
                "evidence": next(iter(evidence[2:]), evidence[-1]),
            },
            "competition_region": {
                "value": editorial_analysis["competition_region"],
                "evidence": next(iter(evidence[3:]), evidence[-1]),
            },
            "prominence": {
                "value": editorial_analysis["prominence"],
                "evidence": next(iter(evidence[4:]), evidence[-1]),
            },
            "skin_tier": {
                "value": editorial_analysis["skin_tier"],
                "evidence": next(iter(evidence[5:]), evidence[-1]),
            },
        }
        proposal = {
            "importance_score": score,
            "importance_evidence": evidence,
            "importance_dimensions": dimensions,
            "importance_policy_version": IMPORTANCE_POLICY_VERSION,
            "importance_calculation": calculation,
            "priority_score": priority_score,
            "priority_calculation": priority_calculation,
            "analysis_model": settings.model_name,
            "_execution_metadata": {
                "importance_scoring": execution_metadata(importance),
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


async def _generate_claim_review(
    db: Session,
    run: ProcessingRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
) -> None:
    try:
        translation = run.context.get("approved_translation_proposal")
        facts = run.context.get("approved_fact_proposal")
        classification = run.context.get("approved_classification_proposal")
        if not isinstance(translation, dict):
            raise ValueError("claim generation stage requires an approved translation proposal")
        if not isinstance(facts, dict):
            raise ValueError("claim generation stage requires an approved fact proposal")
        if not isinstance(classification, dict):
            raise ValueError("claim generation stage requires an approved classification proposal")
        if _is_context_only_without_claimable_mentions(classification):
            proposal = {
                "fact_claims": [],
                "attribution": {
                    "claimed_by": (run.raw_item.author_name or run.raw_item.source.name),
                    "stance": "contextualizes",
                    "certainty": "confirmed",
                },
                "analysis_model": "deterministic",
                "_claim_compaction": {
                    "original_count": 0,
                    "review_count": 0,
                    "strategy": "context-only-no-claims-v1",
                },
                "_execution_metadata": {},
            }
            _replace_pending_review(
                db,
                run=run,
                stage=CLAIM_STAGE,
                proposal=proposal,
            )
            assert_execution_owned(db, execution_guard)
            db.commit()
            return
        assert_execution_owned(db, execution_guard)
        db.commit()
        claims = await LLMClient().generate_claims(
            content=(
                f"{str(facts.get('title') or '').strip()}\n"
                f"{str(facts.get('summary') or '').strip()}"
            ).strip(),
            extracted_facts={
                key: facts[key] for key in ("title", "summary", "entities") if key in facts
            },
            classification={
                key: classification[key]
                for key in (
                    "source_kind",
                    "information_stage",
                    "content_form",
                    "topic",
                    "subtopic",
                    "secondary_topics",
                    "entity_roles",
                    "event_mentions",
                    "event_assertion",
                    "temporal",
                )
                if key in classification
            },
            source_context=_analysis_source_context(run),
        )
        claim_payload = claims.model_dump(mode="json")
        original_claims = list(claim_payload.get("fact_claims") or [])
        compacted_claims = _compact_patch_preview_claims(
            original_claims,
            translation=translation,
            classification=classification,
        )
        proposal = {
            **claim_payload,
            "fact_claims": compacted_claims,
            "analysis_model": settings.model_name,
            "_claim_compaction": {
                "original_count": len(original_claims),
                "review_count": len(compacted_claims),
                "strategy": (
                    "patch-section-groups-v1" if compacted_claims != original_claims else "none"
                ),
            },
            "_execution_metadata": {
                "claim_generation": execution_metadata(claims),
            },
        }
        _replace_pending_review(
            db,
            run=run,
            stage=CLAIM_STAGE,
            proposal=proposal,
        )
        assert_execution_owned(db, execution_guard)
        db.commit()
    except Exception as exc:
        _mark_failed(db, run, exc, execution_guard=execution_guard)
        raise


def _compact_patch_preview_claims(
    fact_claims: list[dict[str, Any]],
    *,
    translation: dict[str, Any],
    classification: dict[str, Any],
) -> list[dict[str, Any]]:
    if str(classification.get("topic") or "") != "patch":
        return fact_claims
    grouped: dict[str, dict[str, Any]] = {}
    grouped_targets: set[str] = set()
    for extraction in translation.get("translated_media_extractions", []):
        if not isinstance(extraction, dict):
            continue
        structured = extraction.get("translated_data")
        if not isinstance(structured, dict):
            continue
        patch = str(structured.get("patch") or "").strip()
        for section in structured.get("sections", []):
            if not isinstance(section, dict):
                continue
            section_type = str(section.get("section_type") or "")
            if section_type not in {
                "champion_buff",
                "champion_nerf",
                "system_buff",
                "system_nerf",
            }:
                continue
            targets = [
                str(entry.get("target") or "").strip()
                for entry in section.get("entries", [])
                if isinstance(entry, dict) and str(entry.get("target") or "").strip()
            ]
            if not targets:
                continue
            grouped_targets.update(targets)
            group = grouped.setdefault(
                section_type,
                {
                    "patch": patch,
                    "targets": [],
                },
            )
            group["targets"].extend(target for target in targets if target not in group["targets"])
    if not grouped:
        return fact_claims

    kept = []
    for claim in fact_claims:
        subject = claim.get("subject")
        subject_name = str(subject.get("name") or "").strip() if isinstance(subject, dict) else ""
        if (
            str(claim.get("predicate") or "") in {"buffs", "nerfs"}
            and subject_name in grouped_targets
        ):
            continue
        kept.append(claim)

    for section_type, group in grouped.items():
        patch = str(group["patch"] or "").strip()
        scope, direction = section_type.split("_", 1)
        kept.append(
            {
                "subject": {
                    "name": f"{patch}版本" if patch else "当前版本",
                    "type": "patch",
                },
                "predicate": "buffs" if direction == "buff" else "nerfs",
                "object": {
                    "scope": scope,
                    "targets": list(group["targets"]),
                    **({"patch": patch} if patch else {}),
                },
                "temporal_role": "prediction",
                "supersedes_hint": None,
            }
        )
    return kept


def _is_context_only_without_claimable_mentions(
    classification: dict[str, Any],
) -> bool:
    if str(classification.get("event_assertion") or "asserted") != "context_only":
        return False
    return not any(
        isinstance(mention, dict) and str(mention.get("assertion") or "asserted") != "context_only"
        for mention in classification.get("event_mentions", [])
    )


def _build_item_proposal(
    *,
    raw_item: RawItem,
    translation_proposal: dict[str, Any],
    fact_proposal: dict[str, Any],
    classification_proposal: dict[str, Any],
    importance_proposal: dict[str, Any],
    claim_proposal: dict[str, Any],
    relevance_proposal: dict[str, Any] | None = None,
    evidence_gate: dict[str, Any] | None = None,
    knowledge_snapshot: list[dict[str, object]] | None = None,
    ocr_corrections: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    facts = fact_proposal
    classification = classification_proposal
    primary_topic = str(classification["topic"])
    roles_by_name = {
        str(role.get("name") or "").strip().casefold(): str(role.get("role") or "context")
        for role in classification.get("entity_roles", [])
        if isinstance(role, dict)
    }
    event_mentions = normalize_event_mentions(
        [
            dict(mention)
            for mention in classification.get("event_mentions", [])
            if isinstance(mention, dict)
        ]
    )
    entity_values = [
        dict(entity) for mention in event_mentions for entity in mention["identity_entities"]
    ]
    entity_values.extend(
        {
            **dict(entity),
            "role": roles_by_name.get(
                str(entity.get("name") or "").strip().casefold(),
                "context",
            ),
        }
        for entity in facts.get("entities", [])
        if isinstance(entity, dict)
    )
    return {
        **translation_proposal,
        "normalized_title": str(facts["title"]),
        "summary": str(facts["summary"]),
        "entities": normalize_entities(entity_values),
        "primary_topic": primary_topic,
        "subtopic": classification["subtopic"],
        "secondary_topics": list(classification.get("secondary_topics") or []),
        "source_kind": classification["source_kind"],
        "information_stage": classification["information_stage"],
        "content_form": classification["content_form"],
        "product_scope": str((relevance_proposal or {}).get("product_scope") or "uncertain"),
        "facets": {
            "product_scope": str((relevance_proposal or {}).get("product_scope") or "uncertain"),
            "region": None,
            "temporal": dict(classification.get("temporal") or {}),
            "event_assertion": str(classification.get("event_assertion") or "asserted"),
            "event_mentions": event_mentions,
            "evidence_gate": dict(evidence_gate or {}),
        },
        "ontology_version": ONTOLOGY_VERSION,
        **{
            key: importance_proposal[key]
            for key in (
                "importance_score",
                "importance_evidence",
                "importance_dimensions",
                "importance_policy_version",
                "importance_calculation",
                "priority_score",
                "priority_calculation",
            )
        },
        "language": raw_item.language,
        "analysis_model": settings.model_name,
        "analysis_version": "pipeline-redesign-p4",
        "fact_claims": list(claim_proposal.get("fact_claims") or []),
        "attribution": dict(claim_proposal.get("attribution") or {}),
        "_execution_metadata": {
            **dict(facts.get("_execution_metadata") or {}),
            **dict(classification.get("_execution_metadata") or {}),
            **dict(importance_proposal.get("_execution_metadata") or {}),
            **dict(claim_proposal.get("_execution_metadata") or {}),
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
            run.current_stage = FACT_CLASSIFY_STAGE
            assert_execution_owned(db, execution_guard)
            db.commit()
            await _generate_fact_review(db, run, **_guard_kwargs(execution_guard))
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
        "primary_topic",
        "subtopic",
        "secondary_topics",
        "source_kind",
        "information_stage",
        "content_form",
        "product_scope",
        "facets",
        "ontology_version",
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
    persist_generated_claims(
        db,
        item,
        fact_claims=list(proposal.get("fact_claims") or []),
        attribution=dict(proposal.get("attribution") or {}),
    )
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
    return bool(proposal.get("is_lol_relevant")) or (proposal.get("product_scope") == "uncertain")


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
        FACT_CLASSIFY_STAGE: {"analysis_correction"},
        IMPORTANCE_STAGE: {"analysis_correction"},
        CLAIM_STAGE: {"analysis_correction"},
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
        "is_official_source": raw_item.source.is_official,
        "published_at": raw_item.published_at.isoformat() if raw_item.published_at else None,
        "is_repost": has_repost_evidence(raw_item.content_blocks),
    }


def _apply_classification_evidence_guardrails(
    proposal: dict[str, Any], raw_item: RawItem
) -> dict[str, Any]:
    guarded = dict(proposal)
    adjustments: list[dict[str, str]] = []
    is_repost = has_repost_evidence(raw_item.content_blocks)
    if is_repost and guarded.get("content_form") != "repost":
        adjustments.append(
            {
                "field": "content_form",
                "from": str(guarded.get("content_form") or ""),
                "to": "repost",
                "reason": "原始证据包含引用来源",
            }
        )
        guarded["content_form"] = "repost"
    if is_repost and guarded.get("source_kind") == "first_party":
        adjustments.append(
            {
                "field": "source_kind",
                "from": "first_party",
                "to": "attributed_report",
                "reason": "转发账号不是被引用事实的直接声明者",
            }
        )
        guarded["source_kind"] = "attributed_report"
    text = (
        f"{raw_item.native_title or ''}\n{text_from_content_blocks(raw_item.content_blocks)}"
    ).casefold()

    def set_field(field: str, value: str, reason: str) -> None:
        previous = str(guarded.get(field) or "")
        if previous == value:
            return
        guarded[field] = value
        adjustments.append({"field": field, "from": previous, "to": value, "reason": reason})

    def rewrite_mentions(
        *,
        eligible_subtopics: set[str] | frozenset[str],
        topic: str | None = None,
        subtopic: str | None = None,
        assertion: str | None = None,
        reason: str,
    ) -> None:
        mentions = list(guarded.get("event_mentions") or [])
        rewritten: list[object] = []
        changed = False
        for value in mentions:
            if (
                not isinstance(value, dict)
                or str(value.get("subtopic") or "") not in eligible_subtopics
            ):
                rewritten.append(value)
                continue
            mention = dict(value)
            if topic is not None and mention.get("topic") != topic:
                mention["topic"] = topic
                changed = True
            if subtopic is not None and mention.get("subtopic") != subtopic:
                mention["subtopic"] = subtopic
                changed = True
            if assertion is not None and mention.get("assertion") != assertion:
                mention["assertion"] = assertion
                changed = True
            rewritten.append(mention)
        if changed:
            guarded["event_mentions"] = rewritten
            adjustments.append(
                {
                    "field": "event_mentions",
                    "from": "model_output",
                    "to": "content_semantics",
                    "reason": reason,
                }
            )

    original_subtopic = str(guarded.get("subtopic") or "")
    if has_hotfix_signal(text) and original_subtopic in {
        "hotfix",
        "patch_notes",
        "champion_update",
        "game_mode_update",
        "maintenance",
        "outage",
    }:
        reason = "不停机更新或热修复属于 hotfix，不按受影响玩法对象分类"
        official_update = raw_item.source.is_official
        set_field("topic", "patch", reason)
        set_field("subtopic", "hotfix", reason)
        set_field("information_stage", "active" if official_update else "preview", reason)
        set_field("event_assertion", "asserted" if official_update else "speculative", reason)
        if not official_update and guarded.get("source_kind") != "data_mined":
            set_field("source_kind", "attributed_report", reason)
        rewrite_mentions(
            eligible_subtopics={
                "hotfix",
                "patch_notes",
                "champion_update",
                "game_mode_update",
                "maintenance",
                "outage",
            },
            topic="patch",
            subtopic="hotfix",
            assertion="asserted" if official_update else "speculative",
            reason=reason,
        )
    elif is_catalog_asset_preview(text) and has_test_environment_signal(text):
        reason = "测试服礼包、商城物料或封面预览按商业内容分类，尚未构成玩法发布"
        set_field("topic", "commerce", reason)
        set_field("subtopic", "shop_offer", reason)
        set_field("information_stage", "preview", reason)
        set_field("event_assertion", "speculative", reason)
        if not raw_item.source.is_official:
            set_field("source_kind", "data_mined", reason)
        if re.search(r"一览|汇总|合集|roundup|catalog", text, re.IGNORECASE):
            set_field("content_form", "roundup", reason)
        if guarded.get("event_mentions"):
            adjustments.append(
                {
                    "field": "event_mentions",
                    "from": str(len(guarded["event_mentions"])),
                    "to": "0",
                    "reason": "资产预览没有明确上架、开售或可获取事实",
                }
            )
            guarded["event_mentions"] = []
    elif has_test_environment_signal(text) and original_subtopic in GAMEPLAY_CHANGE_SUBTOPICS:
        reason = "测试环境玩法变动属于 PBE 预览，不是正式服玩法发布"
        set_field("topic", "patch", reason)
        set_field("subtopic", "pbe_change", reason)
        set_field("information_stage", "preview", reason)
        if not raw_item.source.is_official:
            set_field("source_kind", "data_mined", reason)
            set_field("event_assertion", "speculative", reason)
        rewrite_mentions(
            eligible_subtopics=GAMEPLAY_CHANGE_SUBTOPICS,
            topic="patch",
            subtopic="pbe_change",
            assertion="speculative" if not raw_item.source.is_official else None,
            reason=reason,
        )
    elif (
        original_subtopic in OFFICIAL_ONLY_UPDATE_SUBTOPICS
        and not raw_item.source.is_official
        and str(guarded.get("event_assertion") or "asserted") not in {"context_only", "negated"}
    ):
        reason = "非官方信源的更新或发布统一按爆料处理，只有官方信源可作为正式更新"
        if guarded.get("source_kind") != "data_mined":
            set_field("source_kind", "attributed_report", reason)
        set_field("information_stage", "preview", reason)
        set_field("event_assertion", "speculative", reason)
        rewrite_mentions(
            eligible_subtopics=OFFICIAL_ONLY_UPDATE_SUBTOPICS,
            assertion="speculative",
            reason=reason,
        )
    activity_entities = [
        dict(entity)
        for entity in guarded.get("entities") or []
        if isinstance(entity, dict)
        and entity.get("type") == "activity"
        and str(entity.get("name") or "").strip()
    ]
    is_named_free_reward_opening = (
        bool(activity_entities)
        and _REWARD_CLAIM_EVIDENCE.search(text) is not None
        and _REWARD_CLAIM_OPEN_EVIDENCE.search(text) is not None
        and _LOTTERY_REWARD_EVIDENCE.search(text) is None
        and _PAID_REWARD_EVIDENCE.search(text) is None
    )
    if is_named_free_reward_opening:
        activity = activity_entities[0]
        activity_name = str(activity["name"]).strip()
        canonical_name = str(activity.get("canonical_name") or activity_name).strip()
        previous_topic = str(guarded.get("topic") or "")
        previous_subtopic = str(guarded.get("subtopic") or "")
        previous_stage = str(guarded.get("information_stage") or "")
        previous_assertion = str(guarded.get("event_assertion") or "")
        guarded["topic"] = "activity"
        guarded["subtopic"] = "free_reward"
        guarded["information_stage"] = (
            "active" if guarded.get("source_kind") == "first_party" else "reminder"
        )
        guarded["event_assertion"] = "asserted"
        guarded["event_mentions"] = [
            {
                "topic": "activity",
                "subtopic": "free_reward",
                "identity_entities": [
                    {
                        "name": activity_name,
                        "canonical_name": canonical_name,
                        "type": "activity",
                        "role": "core",
                    }
                ],
                "assertion": "asserted",
                "temporal": dict(guarded.get("temporal") or {}),
                "membership_role": "primary",
            }
        ]
        roles = [
            dict(role)
            for role in guarded.get("entity_roles") or []
            if isinstance(role, dict)
            and str(role.get("name") or "").strip().casefold() != activity_name.casefold()
        ]
        roles.append({"name": activity_name, "role": "core"})
        guarded["entity_roles"] = roles
        reason = "命名活动的确定性奖励已进入领取或开箱阶段"
        for field, previous, current in (
            ("topic", previous_topic, "activity"),
            ("subtopic", previous_subtopic, "free_reward"),
            (
                "information_stage",
                previous_stage,
                str(guarded["information_stage"]),
            ),
            ("event_assertion", previous_assertion, "asserted"),
        ):
            if previous != current:
                adjustments.append(
                    {
                        "field": field,
                        "from": previous,
                        "to": current,
                        "reason": reason,
                    }
                )
        adjustments.append(
            {
                "field": "event_mentions",
                "from": str(len(proposal.get("event_mentions") or [])),
                "to": "1",
                "reason": reason,
            }
        )
    activity_subtopic = str(guarded.get("subtopic") or "")
    corrected_activity_subtopic: str | None = None
    correction_reason: str | None = None
    if activity_subtopic in {"event_pass", "free_reward"} and _LOTTERY_REWARD_EVIDENCE.search(text):
        corrected_activity_subtopic = "in_game_activity"
        correction_reason = "抽奖或概率奖励不是通行证或确定性免费奖励"
    elif activity_subtopic == "event_pass" and not _EVENT_PASS_EVIDENCE.search(text):
        corrected_activity_subtopic = "in_game_activity"
        correction_reason = "原始证据没有通行证、付费等级或里程碑机制"
    if corrected_activity_subtopic is not None:
        adjustments.append(
            {
                "field": "subtopic",
                "from": activity_subtopic,
                "to": corrected_activity_subtopic,
                "reason": str(correction_reason),
            }
        )
        guarded["subtopic"] = corrected_activity_subtopic
        mentions = []
        for index, value in enumerate(guarded.get("event_mentions") or []):
            if not isinstance(value, dict):
                mentions.append(value)
                continue
            mention = dict(value)
            if (
                mention.get("membership_role", "primary") == "primary"
                and mention.get("topic") == "activity"
                and mention.get("subtopic") == activity_subtopic
            ):
                mention["subtopic"] = corrected_activity_subtopic
                adjustments.append(
                    {
                        "field": f"event_mentions[{index}].subtopic",
                        "from": activity_subtopic,
                        "to": corrected_activity_subtopic,
                        "reason": str(correction_reason),
                    }
                )
            mentions.append(mention)
        guarded["event_mentions"] = mentions
    if (
        guarded.get("topic") == "skin"
        and guarded.get("subtopic") == "skin_release"
        and _EVENT_PASS_EVIDENCE.search(text)
        and _ACQUISITION_METHOD_EVIDENCE.search(text)
    ):
        entities = [
            dict(entity) for entity in guarded.get("entities") or [] if isinstance(entity, dict)
        ]
        parents = [
            entity
            for entity in entities
            if entity.get("type") in {"game_mode", "activity"}
            and str(entity.get("name") or "").strip()
        ]
        pass_products = [
            entity
            for entity in entities
            if entity.get("type") == "product"
            and _EVENT_PASS_EVIDENCE.search(str(entity.get("name") or ""))
        ]
        if parents:
            parent_name = str(parents[0]["name"]).strip()
            pass_name = (
                parent_name if _EVENT_PASS_EVIDENCE.search(parent_name) else f"{parent_name}通行证"
            )
        elif pass_products:
            pass_name = re.sub(r"礼包$", "", str(pass_products[0].get("name") or "").strip())
        else:
            pass_name = ""
        guarded["topic"] = "activity"
        guarded["subtopic"] = "event_pass"
        adjustments.extend(
            [
                {
                    "field": "topic",
                    "from": "skin",
                    "to": "activity",
                    "reason": "消息主动作是讨论通行证获取方式而不是发布新外观",
                },
                {
                    "field": "subtopic",
                    "from": "skin_release",
                    "to": "event_pass",
                    "reason": "消息主动作是讨论通行证获取方式而不是发布新外观",
                },
            ]
        )
        mentions = list(guarded.get("event_mentions") or [])
        release_indexes = [
            index
            for index, mention in enumerate(mentions)
            if isinstance(mention, dict)
            and mention.get("topic") == "skin"
            and mention.get("subtopic") == "skin_release"
            and mention.get("membership_role", "primary") in {"primary", "component"}
        ]
        if release_indexes and pass_name:
            first_index = release_indexes[0]
            activity_mention = dict(mentions[first_index])
            activity_mention.update(
                {
                    "topic": "activity",
                    "subtopic": "event_pass",
                    "identity_entities": [
                        {
                            "name": pass_name,
                            "canonical_name": pass_name,
                            "type": "product",
                            "role": "core",
                        }
                    ],
                    "membership_role": "primary",
                }
            )
            release_index_set = set(release_indexes)
            guarded["event_mentions"] = [
                activity_mention if index == first_index else mention
                for index, mention in enumerate(mentions)
                if index not in release_index_set or index == first_index
            ]
    if (
        raw_item.source.connector_type == "baidu_tieba"
        and any(marker in text for marker in ("测试服", "pbe", "物料"))
        and guarded.get("source_kind") != "data_mined"
    ):
        adjustments.append(
            {
                "field": "source_kind",
                "from": str(guarded.get("source_kind") or ""),
                "to": "data_mined",
                "reason": "非官方来源明确发布测试服或客户端物料",
            }
        )
        guarded["source_kind"] = "data_mined"
    context_only = _is_context_only_without_claimable_mentions(guarded)
    if context_only and guarded.get("information_stage") != "commentary":
        adjustments.append(
            {
                "field": "information_stage",
                "from": str(guarded.get("information_stage") or ""),
                "to": "commentary",
                "reason": "消息仅提供上下文观点，没有可断言的事件事实",
            }
        )
        guarded["information_stage"] = "commentary"
    guarded_subtopic = str(guarded.get("subtopic") or "")
    if (
        not raw_item.source.is_official
        and guarded_subtopic in OFFICIAL_ONLY_UPDATE_SUBTOPICS
        and str(guarded.get("event_assertion") or "asserted") not in {"context_only", "negated"}
    ):
        expected_stage = "preview"
    else:
        expected_stage = {
            "patch_preview": "preview",
            "pbe_change": "preview",
            "patch_notes": "active",
            "hotfix": "active",
            "match_result": "result",
            "community_post": "commentary",
        }.get(guarded_subtopic)
    if (
        expected_stage
        and guarded.get("information_stage") != expected_stage
        and not (context_only and expected_stage != "commentary")
    ):
        adjustments.append(
            {
                "field": "information_stage",
                "from": str(guarded.get("information_stage") or ""),
                "to": expected_stage,
                "reason": "子主题包含明确的事实阶段语义",
            }
        )
        guarded["information_stage"] = expected_stage
    if (
        guarded.get("topic") == "skin"
        and guarded.get("subtopic") == "skin_release"
        and guarded.get("content_form") != "roundup"
    ):
        mentions = list(guarded.get("event_mentions") or [])
        release_indexes = [
            index
            for index, mention in enumerate(mentions)
            if isinstance(mention, dict)
            and mention.get("topic") == "skin"
            and mention.get("subtopic") == "skin_release"
            and mention.get("membership_role", "primary") in {"primary", "component"}
        ]
        if len(release_indexes) > 1:
            title = str(guarded.get("title") or raw_item.native_title or "").strip()
            title_key = title.casefold()
            parent_entities = [
                dict(entity)
                for entity in guarded.get("entities") or []
                if isinstance(entity, dict)
                and entity.get("type") == "skin"
                and str(entity.get("name") or "").strip()
                and str(entity.get("name") or "").strip().casefold() in title_key
            ]
            if parent_entities:
                parent = min(
                    parent_entities,
                    key=lambda entity: len(str(entity.get("name") or "")),
                )
                collection_name = str(parent.get("name") or "").strip()
                canonical_name = str(parent.get("canonical_name") or collection_name).strip()
            else:
                collection_name = _RELEASE_ACTION_SUFFIX.sub("", title).strip(" ：:，,。")
                collection_name = collection_name or title
                canonical_name = collection_name
            first_index = release_indexes[0]
            first_mention = dict(mentions[first_index])
            first_mention["identity_entities"] = [
                {
                    "name": collection_name,
                    "canonical_name": canonical_name,
                    "type": "skin",
                    "role": "core",
                }
            ]
            first_mention["membership_role"] = "primary"
            release_index_set = set(release_indexes)
            guarded["event_mentions"] = [
                first_mention if index == first_index else mention
                for index, mention in enumerate(mentions)
                if index not in release_index_set or index == first_index
            ]
            adjustments.append(
                {
                    "field": "event_mentions",
                    "from": str(len(release_indexes)),
                    "to": "1",
                    "reason": "同一公告同批发布的系列外观属于一个发布事件",
                }
            )
        mentions = list(guarded.get("event_mentions") or [])
        filtered_mentions = [
            mention
            for mention in mentions
            if not (isinstance(mention, dict) and mention.get("topic") in {"commerce", "esports"})
        ]
        if len(filtered_mentions) != len(mentions):
            guarded["event_mentions"] = filtered_mentions
            adjustments.append(
                {
                    "field": "event_mentions",
                    "from": str(len(mentions)),
                    "to": str(len(filtered_mentions)),
                    "reason": "外观商业信息和赛事设计背景属于发布事件属性",
                }
            )
    if adjustments:
        guarded["classification_guardrails"] = adjustments
    return guarded


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
