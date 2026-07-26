import json
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.content_blocks import has_quoted_post, text_from_content_blocks
from app.models.event_item import EventItem
from app.models.event_revision import EventRevision
from app.models.news_event import NewsEvent
from app.models.media_extraction import MediaExtraction
from app.models.normalized_item import NormalizedItem
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
EVENT_STAGE = "event"

EVENT_WINDOWS_DAYS = {
    "hotfix": 7,
    "incident": 7,
    "esports_match": 7,
    "patch_preview": 14,
    "game_update": 21,
    "skin_leak": 30,
    "skin_release": 30,
    "tournament": 60,
    "roster_change": 90,
    "announcement": 90,
    "other": 30,
}

OFFICIAL_CONNECTORS = {"riot_official", "tencent_lol"}
OFFICIAL_ACCOUNTS = {
    "leagueoflegends",
    "lolesports",
    "riotphroxzon",
    "5756404150",
    "5720474518",
}


async def start_item_processing(db: Session, raw_item: RawItem) -> ProcessingRun:
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
    run = ProcessingRun(
        raw_item_id=raw_item.id,
        workflow_type="item",
        status="running",
        current_stage=RELEVANCE_STAGE,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    await _generate_relevance_review(db, run)
    return run


async def start_event_processing(db: Session, item: NormalizedItem) -> ProcessingRun:
    if item.event_status in {"linked", "not_event"}:
        raise ValueError(f"normalized item event processing is already {item.event_status}")
    active = db.scalar(
        select(ProcessingRun).where(
            ProcessingRun.raw_item_id == item.raw_item_id,
            ProcessingRun.workflow_type == "event",
            ProcessingRun.status.in_(["running", "awaiting_review"]),
        )
    )
    if active:
        raise ValueError(f"normalized item already has active event run {active.id}")
    run = ProcessingRun(
        raw_item_id=item.raw_item_id,
        workflow_type="event",
        status="running",
        current_stage=EVENT_STAGE,
        context={"normalized_item_id": item.id},
    )
    db.add(run)
    item.event_status = "processing"
    db.commit()
    db.refresh(run)
    await _generate_event_review(db, run, item)
    return run


async def retry_processing_run(db: Session, run: ProcessingRun) -> ProcessingRun:
    if run.status not in {"revision_requested", "failed"}:
        raise ValueError(f"processing run cannot retry from status={run.status}")
    run.status = "running"
    run.error_message = None
    db.commit()
    if run.current_stage == RELEVANCE_STAGE:
        await _generate_relevance_review(db, run)
    elif run.current_stage == OCR_STAGE:
        await _generate_ocr_review(db, run)
    elif run.current_stage == ITEM_STAGE:
        if (
            is_patch_preview(run.raw_item)
            and not run.context.get("approved_media_extraction_ids")
        ):
            run.current_stage = OCR_STAGE
            db.commit()
            await _generate_ocr_review(db, run)
        else:
            await _generate_item_review(db, run)
    elif run.current_stage == EVENT_STAGE:
        item_id = int(run.context["normalized_item_id"])
        item = db.get(NormalizedItem, item_id)
        if not item:
            raise LookupError(f"normalized item not found: {item_id}")
        await _generate_event_review(db, run, item)
    else:
        raise ValueError(f"unknown processing stage: {run.current_stage}")
    return run


async def approve_review(db: Session, review: ReviewTask, *, note: str | None) -> ProcessingRun:
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
            run.completed_at = now
            db.commit()
    elif review.stage == OCR_STAGE:
        extraction_ids = [
            int(value)
            for value in review.proposal.get("approved_media_extraction_ids", [])
            if isinstance(value, int)
        ]
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
            structure_patch_extraction(
                extraction,
                title=run.raw_item.display_title,
            )
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
        run.completed_at = now
        db.commit()
    elif review.stage == EVENT_STAGE:
        _apply_event_proposal(db, review)
        run.status = "completed"
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
    now = datetime.now(UTC)
    feedback = payload.model_dump(mode="json")
    review.status = "rejected"
    review.feedback = feedback
    review.resolved_at = now
    run = review.processing_run
    run.status = "revision_requested"
    run.current_stage = review.stage
    if review.stage == ITEM_STAGE:
        extraction_ids = [
            int(value)
            for value in review.proposal.get("approved_media_extraction_ids", [])
            if isinstance(value, int)
        ]
        if extraction_ids:
            run.context = {
                **run.context,
                "approved_media_extraction_ids": extraction_ids,
            }

    grows_rule = payload.feedback_type in {
        "relevance_correction",
        "analysis_correction",
        "event_correction",
    }
    if grows_rule:
        knowledge_type = {
            RELEVANCE_STAGE: "relevance",
            ITEM_STAGE: "analysis",
            EVENT_STAGE: "event_aggregation",
        }[review.stage]
        db.add(
            KnowledgeRule(
                knowledge_type=knowledge_type,
                scope=payload.knowledge_scope,
                rule_text=payload.knowledge_rule or payload.reason,
                correction_data=payload.corrected_values,
                source_review_id=review.id,
                is_active=True,
            )
        )
    glossary_updates = (
        payload.glossary_updates if payload.feedback_type == "translation_term" else []
    )
    for correction in glossary_updates:
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
    approved_ids = [
        int(value)
        for value in review.proposal.get("approved_media_extraction_ids", [])
        if isinstance(value, int)
    ]
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
    replacements = {
        extraction.id: extraction
        for extraction in db.scalars(
            select(MediaExtraction).where(MediaExtraction.id.in_(replacement_ids))
        )
    }
    media_extractions = [
        replacements[extraction_id]
        for extraction_id in replacement_ids
        if extraction_id in replacements
    ]
    correction_history = list(review.proposal.get("ocr_corrections", []))
    correction_history.append(
        {
            "corrected_from_extraction_id": original.id,
            "corrected_extraction_id": corrected.id,
            "note": payload.note,
        }
    )
    proposal = {
        "approved_media_extraction_ids": [item.id for item in media_extractions],
        "ocr_corrections": correction_history,
    }
    _replace_pending_review(db, run=run, stage=OCR_STAGE, proposal=proposal)
    db.commit()
    db.refresh(run)
    return run


async def _generate_relevance_review(db: Session, run: ProcessingRun) -> None:
    raw_item = run.raw_item
    rules = _knowledge_texts(db, "relevance", raw_item)
    try:
        result = await LLMClient().judge_relevance(
            title=raw_item.display_title,
            content=text_from_content_blocks(raw_item.content_blocks),
            source_context=_source_context(raw_item),
            knowledge_rules=rules,
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


async def _generate_item_review(db: Session, run: ProcessingRun) -> None:
    raw_item = run.raw_item
    glossary = _glossary_payload(db)
    rules = _knowledge_texts(db, "analysis", raw_item)
    try:
        extraction_ids = [
            int(value)
            for value in run.context.get("approved_media_extraction_ids", [])
            if isinstance(value, int)
        ]
        media_extractions = list(
            db.scalars(
                select(MediaExtraction)
                .where(MediaExtraction.id.in_(extraction_ids))
                .order_by(MediaExtraction.id)
            )
        )
        proposal = await _build_item_proposal(
            raw_item=raw_item,
            media_extractions=media_extractions,
            glossary=glossary,
            rules=rules,
        )
        _replace_pending_review(db, run=run, stage=ITEM_STAGE, proposal=proposal)
        db.commit()
    except Exception as exc:
        _mark_failed(db, run, exc)
        raise


async def _generate_ocr_review(db: Session, run: ProcessingRun) -> None:
    raw_item = run.raw_item
    try:
        media_extractions = await understand_patch_media(
            db,
            raw_item,
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
    translation = await build_translation(
        raw_item,
        canonical_title=analysis.title,
        glossary=glossary,
    )
    display_title = (
        translation.translated_title
        if translation.translation_status == "translated"
        else analysis.title
    )
    return {
        "normalized_title": display_title,
        "normalized_text": source_text,
        "summary": analysis.summary,
        "category": analysis.category,
        "entities": analysis.entities,
        "importance_score": analysis.importance_score,
        "credibility": analysis.credibility,
        "language": raw_item.language,
        "source_language": translation.source_language,
        "target_language": translation.target_language,
        "translated_title": translation.translated_title,
        "translated_text": translation.translated_text,
        "translated_content_blocks": translation.translated_content_blocks,
        "translation_status": translation.translation_status,
        "translation_model": translation.translation_model,
        "analysis_model": settings.model_name,
        "analysis_version": "v3-reviewed",
        "approved_media_extraction_ids": [item.id for item in media_extractions],
        "media_extractions": structured_context,
        "ocr_corrections": ocr_corrections or [],
        "glossary_term_ids": [
            int(term["id"]) for term in glossary if isinstance(term.get("id"), int)
        ],
    }


async def _generate_event_review(
    db: Session,
    run: ProcessingRun,
    item: NormalizedItem,
) -> None:
    rules = _knowledge_texts(db, "event_aggregation", item.raw_item)
    item_payload = _normalized_payload(item)
    try:
        profile = await LLMClient().classify_event(item=item_payload, knowledge_rules=rules)
        if not profile.is_event:
            proposal = {
                "is_event": False,
                "profile": profile.model_dump(mode="json"),
                "candidate_events": [],
            }
        else:
            candidates = _find_candidate_events(db, item, profile.model_dump(mode="json"))
            resolution = await LLMClient().resolve_event(
                item=item_payload,
                profile=profile.model_dump(mode="json"),
                candidates=candidates,
                source_context=_source_context(item.raw_item),
                knowledge_rules=rules,
            )
            proposal = {
                "is_event": True,
                "profile": profile.model_dump(mode="json"),
                "resolution": resolution.model_dump(mode="json"),
                "candidate_events": candidates,
            }
        _replace_pending_review(db, run=run, stage=EVENT_STAGE, proposal=proposal)
        item.event_status = "event_review"
        db.commit()
    except Exception as exc:
        _mark_failed(db, run, exc)
        raise


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
        "language",
        "source_language",
        "target_language",
        "translated_title",
        "translated_text",
        "translated_content_blocks",
        "approved_media_extraction_ids",
        "translation_status",
        "translation_model",
        "analysis_model",
        "analysis_version",
    }
    item = NormalizedItem(
        raw_item_id=raw_item.id,
        event_status="pending",
        **{key: value for key, value in proposal.items() if key in allowed_fields},
    )
    db.add(item)
    db.flush()
    return item


def _apply_event_proposal(db: Session, review: ReviewTask) -> None:
    proposal = review.proposal
    run = review.processing_run
    item_id = int(run.context["normalized_item_id"])
    item = db.get(NormalizedItem, item_id)
    if not item:
        raise LookupError(f"normalized item not found: {item_id}")
    if not proposal.get("is_event"):
        item.event_status = "not_event"
        return

    resolution = dict(proposal["resolution"])
    action = resolution["action"]
    published_at = item.raw_item.published_at or item.raw_item.ingested_at
    if action == "create":
        event = NewsEvent(
            title=resolution["title"],
            summary=resolution["summary"],
            category=resolution["category"],
            event_type=resolution["event_type"],
            entities=resolution["entities"],
            importance_score=resolution["importance_score"],
            credibility=resolution["credibility"],
            status="active",
            primary_item_id=item.id,
            first_published_at=published_at,
            last_activity_at=published_at,
            occurred_at=_parse_optional_datetime(proposal["profile"].get("occurred_at")),
        )
        db.add(event)
        db.flush()
        change_type = "created"
    else:
        event = db.get(NewsEvent, int(resolution["event_id"]))
        if not event:
            raise LookupError(f"candidate event not found: {resolution['event_id']}")
        event.title = resolution["title"]
        event.summary = resolution["summary"]
        event.category = resolution["category"]
        event.event_type = resolution["event_type"]
        event.entities = resolution["entities"]
        event.importance_score = resolution["importance_score"]
        event.credibility = resolution["credibility"]
        event.last_activity_at = max(
            [value for value in (event.last_activity_at, published_at) if value is not None]
        )
        if event.first_published_at is None or (
            published_at is not None and published_at < event.first_published_at
        ):
            event.first_published_at = published_at
        if _source_authority(item.raw_item) > _primary_authority(db, event):
            event.primary_item_id = item.id
        change_type = "updated"

    existing_link = db.get(
        EventItem,
        {"event_id": event.id, "normalized_item_id": item.id},
    )
    if not existing_link:
        db.add(
            EventItem(
                event_id=event.id,
                normalized_item_id=item.id,
                relation_type=resolution["relation_type"],
                is_primary=event.primary_item_id == item.id,
            )
        )
    for link in event.items:
        link.is_primary = link.normalized_item_id == event.primary_item_id
    db.flush()
    next_version = int(
        db.scalar(
            select(func.coalesce(func.max(EventRevision.version), 0)).where(
                EventRevision.event_id == event.id
            )
        )
        or 0
    ) + 1
    db.add(
        EventRevision(
            event_id=event.id,
            version=next_version,
            change_type=change_type,
            snapshot=_event_snapshot(event, resolution),
            source_review_id=review.id,
        )
    )
    item.event_status = "linked"


def _find_candidate_events(
    db: Session,
    item: NormalizedItem,
    profile: dict[str, Any],
) -> list[dict[str, object]]:
    event_type = str(profile.get("event_type") or "other")
    days = EVENT_WINDOWS_DAYS.get(event_type, 30)
    anchor = item.raw_item.published_at or item.raw_item.ingested_at or datetime.now(UTC)
    cutoff = anchor - timedelta(days=days)
    events = list(
        db.scalars(
            select(NewsEvent).where(
                NewsEvent.status.in_(["active", "monitoring"]),
                NewsEvent.last_activity_at >= cutoff,
                NewsEvent.last_activity_at <= anchor + timedelta(days=2),
            )
        )
    )
    incoming_entities = _entity_names(item.entities)
    incoming_title = str(profile.get("title") or item.normalized_title)
    ranked: list[tuple[float, NewsEvent]] = []
    for event in events:
        entity_overlap = len(incoming_entities & _entity_names(event.entities))
        title_similarity = SequenceMatcher(
            None, incoming_title.casefold(), event.title.casefold()
        ).ratio()
        type_bonus = 0.3 if event.event_type == event_type else 0.0
        category_bonus = 0.15 if event.category == item.category else 0.0
        ranked.append((entity_overlap * 0.4 + title_similarity + type_bonus + category_bonus, event))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            "id": event.id,
            "title": event.title,
            "summary": event.summary,
            "category": event.category,
            "event_type": event.event_type,
            "entities": event.entities,
            "credibility": event.credibility,
            "first_published_at": _iso(event.first_published_at),
            "last_activity_at": _iso(event.last_activity_at),
            "retrieval_score": round(score, 4),
        }
        for score, event in ranked[:5]
    ]


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
        "published_at": _iso(raw_item.published_at),
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


def _primary_authority(db: Session, event: NewsEvent) -> int:
    if not event.primary_item_id:
        return -1
    item = db.get(NormalizedItem, event.primary_item_id)
    return _source_authority(item.raw_item) if item else -1


def _normalized_payload(item: NormalizedItem) -> dict[str, object]:
    return {
        "id": item.id,
        "title": item.normalized_title,
        "summary": item.summary,
        "category": item.category,
        "entities": item.entities,
        "importance_score": item.importance_score,
        "credibility": item.credibility,
        "published_at": _iso(item.raw_item.published_at),
        "source": _source_context(item.raw_item),
    }


def _event_snapshot(event: NewsEvent, resolution: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event.id,
        "title": event.title,
        "summary": event.summary,
        "category": event.category,
        "event_type": event.event_type,
        "entities": event.entities,
        "importance_score": event.importance_score,
        "credibility": event.credibility,
        "status": event.status,
        "primary_item_id": event.primary_item_id,
        "first_published_at": _iso(event.first_published_at),
        "last_activity_at": _iso(event.last_activity_at),
        "occurred_at": _iso(event.occurred_at),
        "adds_new_information": resolution.get("adds_new_information"),
        "conflicts": resolution.get("conflicts", []),
        "reason": resolution.get("reason"),
    }


def _entity_names(entities: list[dict[str, Any]]) -> set[str]:
    return {
        str(entity.get("canonical_name") or entity.get("name") or "").casefold()
        for entity in entities
        if entity.get("canonical_name") or entity.get("name")
    }


def _parse_optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _require_pending_review(review: ReviewTask) -> None:
    if review.status != "pending":
        raise ValueError(f"review task cannot be resolved from status={review.status}")


def _mark_failed(db: Session, run: ProcessingRun, exc: Exception) -> None:
    db.rollback()
    failed = db.get(ProcessingRun, run.id)
    if failed:
        failed.status = "failed"
        failed.error_message = str(exc)[:4000]
        db.commit()
