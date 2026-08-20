import asyncio
import hashlib
import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.content_blocks import has_repost_evidence, text_from_content_blocks
from app.core.config import settings
from app.domain.evidence import evaluate_evidence_gate
from app.domain.importance import (
    IMPORTANCE_POLICY_VERSION,
    calculate_importance,
    calculate_message_priority,
    derive_importance_profile,
    normalize_importance_features,
)
from app.models.media_asset import MediaAsset
from app.models.media_extraction import MediaExtraction
from app.models.normalized_item import NormalizedItemRevision
from app.models.ocr_lab import OCRProfile
from app.models.pipeline import ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.workflow import ProcessingRun
from app.orchestration.contracts import (
    ITEM_PROCESSING_GRAPH,
    ITEM_PROCESSING_GRAPH_VERSION,
    ITEM_PROCESSING_STATE_VERSION,
    PROCESSING_STAGE_ORDER,
    BusinessStageCheckpoint,
    CheckpointReceipt,
    EvidenceGateProposal,
    EvidenceSnapshot,
    ImportanceProposal,
    ItemProcessingRequest,
    MediaProposal,
    MessageAnalysisProposal,
    ProcessingStage,
    PublicationResult,
    RelevanceProposal,
    ReplayPrefix,
    ReviewDecision,
    ReviewMode,
    RunMode,
    TranslationProposal,
)
from app.services.classification_source import resolve_classification_source
from app.services.llm import LLMClient, execution_metadata
from app.services.media_ocr import run_ocr
from app.services.pipeline_queue import enqueue_pipeline_job
from app.services.raw_item_versions import is_latest_raw_item
from app.services.patch_table import parse_patch_table
from app.workflows.reviewed_pipeline import (
    _analysis_content,
    _apply_normalized_item,
    _build_item_proposal,
    _glossary_payload,
    _importance_scoring_content,
    _knowledge_rule_snapshot,
    _knowledge_texts_from_snapshot,
    _message_analysis_content,
    _source_context,
)
from app.workflows.translate_item import build_translation
from app.workflows.understand_media import (
    DETERMINISTIC_STRUCTURE_VERSION,
    PATCH_SCHEMA_VERSION,
    PATCH_TASK,
    build_patch_preview,
    extraction_context,
    is_patch_preview,
)


SessionFactory = Callable[[], Session]
LLMFactory = Callable[[], LLMClient]


def _raw_statement(raw_item_id: int):
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


def _load_raw(db: Session, raw_item_id: int) -> RawItem:
    raw_item = db.scalar(_raw_statement(raw_item_id))
    if raw_item is None:
        raise ValueError(f"raw item {raw_item_id} not found")
    return raw_item


def _translation_payload(proposal: TranslationProposal) -> dict[str, Any]:
    value = proposal.model_dump(mode="json")
    value["translation_status"] = value.pop("status")
    value["approved_media_extraction_ids"] = [
        int(extraction["extraction_id"])
        for extraction in proposal.translated_media_extractions
        if isinstance(extraction.get("extraction_id"), int)
    ]
    return value


def _analysis_payload(proposal: MessageAnalysisProposal) -> dict[str, Any]:
    value = proposal.model_dump(mode="json")
    value["_execution_metadata"] = value.pop("execution_metadata")
    return value


def _importance_payload(proposal: ImportanceProposal) -> dict[str, Any]:
    value = proposal.model_dump(mode="json")
    value["importance_calculation"] = value.pop("calculation")
    value["_execution_metadata"] = value.pop("execution_metadata")
    return value


class V2CompatibilityItemBackend:
    """Run proven v2 item semantics under the v3 LangGraph contract.

    This is a migration adapter. Experimental algorithms should implement the
    same backend protocol without importing private v2 workflow helpers.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        llm_factory: LLMFactory = LLMClient,
    ) -> None:
        self._session_factory = session_factory
        self._llm_factory = llm_factory

    async def load_evidence(self, request: ItemProcessingRequest) -> EvidenceSnapshot:
        with self._session_factory() as db:
            raw_item = _load_raw(db, request.raw_item_id)
            if raw_item.revision != request.raw_item_revision:
                raise ValueError("raw item revision does not match graph request")
            payload = {
                "raw_item_id": raw_item.id,
                "revision": raw_item.revision,
                "content_hash": raw_item.content_hash,
                "title": raw_item.native_title,
                "content_blocks": raw_item.content_blocks,
                "media_asset_ids": [asset.id for asset in raw_item.media_assets],
            }
            fingerprint = hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            return EvidenceSnapshot(
                raw_item_id=raw_item.id,
                raw_item_revision=raw_item.revision,
                title=raw_item.display_title or "",
                language=raw_item.language,
                text=text_from_content_blocks(raw_item.content_blocks),
                media_asset_ids=[asset.id for asset in raw_item.media_assets],
                evidence_fingerprint=fingerprint,
            )

    async def judge_relevance(self, evidence: EvidenceSnapshot) -> RelevanceProposal:
        with self._session_factory() as db:
            raw_item = _load_raw(db, evidence.raw_item_id)
            gate = evaluate_evidence_gate(
                raw_item,
                designer_patch_images=is_patch_preview(raw_item),
            ).as_dict()
            source_context = {**_source_context(raw_item), "evidence_gate": gate}
            title = raw_item.display_title
            content = text_from_content_blocks(raw_item.content_blocks)
        result = await self._llm_factory().judge_relevance(
            title=title,
            content=content,
            source_context=source_context,
        )
        return RelevanceProposal(
            **result.model_dump(mode="json"),
            execution_metadata=execution_metadata(result),
        )

    async def understand_media(self, evidence: EvidenceSnapshot) -> MediaProposal:
        with self._session_factory() as db:
            raw_item = _load_raw(db, evidence.raw_item_id)
            if not is_patch_preview(raw_item):
                return MediaProposal()
            profile = db.scalar(
                select(OCRProfile)
                .where(OCRProfile.is_active.is_(True))
                .order_by(OCRProfile.updated_at.desc())
                .limit(1)
            )
            parameters = dict(profile.parameters) if profile else {}
            profile_id = profile.id if profile else None
            profile_name = profile.name if profile else "rapidocr-default"
            assets = [
                (asset.id, asset.storage_path)
                for asset in raw_item.media_assets
                if asset.mime_type is None or asset.mime_type.startswith("image/")
            ]
            title = raw_item.display_title

        extraction_ids: list[int] = []
        for asset_id, asset_path in assets:
            with self._session_factory() as db:
                existing = db.scalar(
                    select(MediaExtraction)
                    .where(
                        MediaExtraction.media_asset_id == asset_id,
                        MediaExtraction.task_type == PATCH_TASK,
                        MediaExtraction.schema_version == PATCH_SCHEMA_VERSION,
                        MediaExtraction.status == "processed",
                    )
                    .order_by(MediaExtraction.created_at.desc())
                    .limit(1)
                )
                if (
                    existing is not None
                    and existing.processing_config.get("parameters") == parameters
                ):
                    extraction_ids.append(existing.id)
                    continue
            if not asset_path:
                raise RuntimeError(f"media asset {asset_id} has no storage path")
            ocr = await asyncio.to_thread(run_ocr, asset_path, parameters)
            table = await asyncio.to_thread(
                parse_patch_table,
                asset_path,
                ocr,
                parameters,
                title_hint=title,
            )
            structured = build_patch_preview(title=title, table_data=table.model_dump())
            with self._session_factory() as db:
                asset = db.get(MediaAsset, asset_id)
                if asset is None:
                    raise ValueError(f"media asset {asset_id} disappeared")
                for stale in db.scalars(
                    select(MediaExtraction).where(
                        MediaExtraction.media_asset_id == asset.id,
                        MediaExtraction.task_type == PATCH_TASK,
                        MediaExtraction.status == "processed",
                    )
                ):
                    stale.status = "superseded"
                asset.sha256 = ocr.sha256
                asset.width = ocr.width
                asset.height = ocr.height
                asset.ocr_text = ocr.raw_text
                extraction = MediaExtraction(
                    media_asset_id=asset.id,
                    task_type=PATCH_TASK,
                    provider=f"patch-table+rapidocr+{DETERMINISTIC_STRUCTURE_VERSION}",
                    ocr_engine=ocr.engine,
                    structuring_model=DETERMINISTIC_STRUCTURE_VERSION,
                    schema_version=PATCH_SCHEMA_VERSION,
                    status="processed",
                    raw_ocr_text=ocr.raw_text,
                    ocr_lines=ocr.lines,
                    structured_data=structured.model_dump(mode="json"),
                    processing_config={
                        "ocr_profile_id": profile_id,
                        "ocr_profile_name": profile_name,
                        "parameters": parameters,
                        "processed_width": ocr.processed_width,
                        "processed_height": ocr.processed_height,
                        "table_data": table.model_dump(),
                        "structure_confidence": table.structure_confidence,
                    },
                    confidence=min(ocr.confidence, table.structure_confidence),
                )
                db.add(extraction)
                db.commit()
                db.refresh(extraction)
                extraction_ids.append(extraction.id)
        with self._session_factory() as db:
            extractions = list(
                db.scalars(
                    select(MediaExtraction)
                    .where(MediaExtraction.id.in_(extraction_ids))
                    .order_by(MediaExtraction.id)
                )
            )
            return MediaProposal(
                requires_ocr=bool(extraction_ids),
                extraction_ids=extraction_ids,
                structured_media=extraction_context(extractions),
            )

    async def translate(
        self,
        evidence: EvidenceSnapshot,
        media: MediaProposal,
    ) -> TranslationProposal:
        with self._session_factory() as db:
            raw_item = _load_raw(db, evidence.raw_item_id)
            extractions = list(
                db.scalars(
                    select(MediaExtraction)
                    .where(MediaExtraction.id.in_(media.extraction_ids))
                    .order_by(MediaExtraction.id)
                )
            )
            glossary = _glossary_payload(
                db, text_from_content_blocks(raw_item.content_blocks)
            )
            rules = _knowledge_rule_snapshot(db, "translation", raw_item)
            db.expunge_all()
        translation = await build_translation(
            raw_item,
            media_extractions=extractions,
            glossary=glossary,
            rules=_knowledge_texts_from_snapshot(rules),
            client=self._llm_factory(),
        )
        return TranslationProposal(
            status=translation.translation_status,
            normalized_text=evidence.text,
            language=evidence.language,
            source_language=translation.source_language,
            target_language=translation.target_language,
            translated_title=translation.translated_title,
            translated_text=translation.translated_text,
            translated_content_blocks=translation.translated_content_blocks,
            translated_media_extractions=translation.translated_media_extractions,
            glossary_term_ids=[
                int(term["id"])
                for term in glossary
                if isinstance(term.get("id"), int)
            ],
            knowledge_rules=rules,
            translation_model=translation.translation_model,
        )

    async def analyze_message(
        self,
        evidence: EvidenceSnapshot,
        media: MediaProposal,
        translation: TranslationProposal,
    ) -> MessageAnalysisProposal:
        translation_payload = _translation_payload(translation)
        with self._session_factory() as db:
            raw_item = _load_raw(db, evidence.raw_item_id)
            rules = _knowledge_rule_snapshot(db, "analysis", raw_item)
            source_context = {
                **_source_context(raw_item),
                "evidence_gate": evaluate_evidence_gate(
                    raw_item,
                    designer_patch_images=is_patch_preview(raw_item),
                    designer_patch_extraction_count=len(media.extraction_ids),
                ).as_dict(),
            }
            evidence_structure = {
                "content_block_types": [
                    str(block.get("type") or "")
                    for block in raw_item.content_blocks
                    if isinstance(block, dict)
                ],
                "translated_block_types": [
                    str(block.get("type") or "")
                    for block in translation.translated_content_blocks
                ],
                "canonical_url": raw_item.canonical_url,
                "has_repost_evidence": has_repost_evidence(raw_item.content_blocks),
            }
        result = await self._llm_factory().analyze_message_content(
            title=translation.translated_title,
            content=_message_analysis_content(translation_payload),
            evidence_structure=evidence_structure,
            source_context=source_context,
            knowledge_rules=_knowledge_texts_from_snapshot(rules),
        )
        with self._session_factory() as db:
            raw_item = _load_raw(db, evidence.raw_item_id)
            classification_source = resolve_classification_source(
                db, raw_item, content_form=result.content_form
            )
        return MessageAnalysisProposal(
            **result.model_dump(mode="json"),
            classification_source=classification_source,
            knowledge_rules=rules,
            analysis_model=settings.model_name,
            execution_metadata={"message_analysis": execution_metadata(result)},
        )

    async def score_importance(
        self,
        evidence: EvidenceSnapshot,
        translation: TranslationProposal,
        analysis: MessageAnalysisProposal,
    ) -> ImportanceProposal:
        if analysis.content_form in {"media_only", "link_only"}:
            return ImportanceProposal(
                message_type="unknown",
                topics=["unknown"],
                importance_score=0,
                classification_source=analysis.classification_source,
                importance_policy_version=IMPORTANCE_POLICY_VERSION,
                calculation={"skipped_reason": analysis.content_form},
                priority_calculation={"skipped_reason": analysis.content_form},
                analysis_model=settings.model_name,
            )
        with self._session_factory() as db:
            raw_item = _load_raw(db, evidence.raw_item_id)
            source_context = {
                **_source_context(raw_item),
                "classification_source": analysis.classification_source,
                "classification_source_kind": str(
                    analysis.classification_source.get("source_kind") or "unknown"
                ),
            }
        translation_payload = _translation_payload(translation)
        scoring_content = _importance_scoring_content(
            translation_payload, analysis.model_dump(mode="json")
        )
        result = await self._llm_factory().classify_and_score_importance(
            content=_analysis_content(translation_payload),
            extracted_facts={
                key: value
                for key, value in analysis.model_dump(mode="json").items()
                if key in {"title", "summary", "entities", "products", "content_form"}
            },
            products=analysis.products,
            content_form=analysis.content_form,
            source_context=source_context,
            knowledge_rules=_knowledge_texts_from_snapshot(analysis.knowledge_rules),
        )
        result_payload = result.model_dump(mode="json")
        message_type = str(result_payload.pop("message_type"))
        topics = list(result_payload.pop("topics"))
        profile = derive_importance_profile(
            message_type=message_type, topics=topics, content=scoring_content
        )
        features = normalize_importance_features(
            result_payload, profile=profile, content=scoring_content
        )
        score, calculation = calculate_importance(
            features,
            message_type=message_type,
            topics=topics,
            content_form=analysis.content_form,
            content=scoring_content,
        )
        priority_score, priority_calculation = calculate_message_priority(
            score,
            content_form=analysis.content_form,
            audience_region=str(features["audience_region"]),
        )
        evidence_values = list(features["evidence"])
        dimensions = {
            name: {"value": features[name], "evidence": evidence_values[0]}
            for name in (
                "scale",
                "audience_region",
                "competition_region",
                "prominence",
                "skin_tier",
            )
        }
        dimensions["importance_profile"] = {
            "value": calculation["importance_profile"],
            "evidence": evidence_values[0],
        }
        return ImportanceProposal(
            message_type=message_type,
            topics=topics,
            classification_source=analysis.classification_source,
            importance_score=score,
            importance_evidence=evidence_values,
            importance_dimensions=dimensions,
            importance_policy_version=IMPORTANCE_POLICY_VERSION,
            calculation=calculation,
            priority_score=priority_score,
            priority_calculation=priority_calculation,
            analysis_model=settings.model_name,
            execution_metadata={
                "classification_importance": execution_metadata(result)
            },
        )

    async def evaluate_evidence(
        self,
        evidence: EvidenceSnapshot,
        analysis: MessageAnalysisProposal,
        importance: ImportanceProposal,
    ) -> EvidenceGateProposal:
        del analysis, importance
        with self._session_factory() as db:
            raw_item = _load_raw(db, evidence.raw_item_id)
            extraction = db.scalar(
                select(MediaExtraction.id)
                .join(MediaAsset)
                .where(
                    MediaAsset.raw_item_id == raw_item.id,
                    MediaExtraction.status == "processed",
                )
                .limit(1)
            )
            gate = evaluate_evidence_gate(
                raw_item,
                designer_patch_images=is_patch_preview(raw_item),
                designer_patch_extraction_count=1 if extraction else 0,
            )
        return EvidenceGateProposal(
            decision="accept",
            reasons=[gate.reason],
            reason_code=gate.reason_code,
            evidence_sources=list(gate.evidence_sources),
            meaningful_text_characters=gate.meaningful_text_characters,
            designer_patch_extraction_count=gate.designer_patch_extraction_count,
        )

    async def save_checkpoint(
        self,
        request: ItemProcessingRequest,
        checkpoint: BusinessStageCheckpoint,
    ) -> CheckpointReceipt:
        if request.run_mode != RunMode.PRODUCTION:
            return CheckpointReceipt(
                checkpoint_id=PROCESSING_STAGE_ORDER.index(checkpoint.stage) + 1,
                workflow_run_id=request.workflow_run_id,
                stage=checkpoint.stage,
            )
        key = (
            f"{ITEM_PROCESSING_GRAPH}:{request.graph_version}:"
            f"run:{request.workflow_run_id}:stage:{checkpoint.stage.value}"
        )
        with self._session_factory() as db:
            existing = db.scalar(
                select(ProcessingCheckpoint).where(
                    ProcessingCheckpoint.idempotency_key == key
                )
            )
            if existing is not None:
                return CheckpointReceipt(
                    checkpoint_id=existing.id,
                    workflow_run_id=request.workflow_run_id,
                    stage=checkpoint.stage,
                )
            run = db.get(ProcessingRun, request.workflow_run_id)
            if run is None or run.raw_item_id != request.raw_item_id:
                raise ValueError("processing run does not match graph request")
            record = ProcessingCheckpoint(
                raw_item_id=request.raw_item_id,
                processing_run_id=request.workflow_run_id,
                stage=checkpoint.stage.value,
                graph_name=ITEM_PROCESSING_GRAPH,
                graph_version=request.graph_version,
                state_version=request.state_version,
                evidence_fingerprint=checkpoint.evidence_fingerprint,
                idempotency_key=key,
                upstream_checkpoint_ids=checkpoint.upstream_checkpoint_ids,
                output_snapshot=checkpoint.output_snapshot,
                artifact_references={
                    "review_decision": (
                        checkpoint.review_decision.model_dump(mode="json")
                        if checkpoint.review_decision is not None
                        else None
                    )
                },
                decision_source=(
                    "automatic"
                    if checkpoint.review_decision is None
                    or checkpoint.review_decision.note == "automatic policy approval"
                    else "manual"
                ),
            )
            run.current_stage = checkpoint.stage.value
            db.add(record)
            db.commit()
            db.refresh(record)
            return CheckpointReceipt(
                checkpoint_id=record.id,
                workflow_run_id=request.workflow_run_id,
                stage=checkpoint.stage,
            )

    async def restore_replay_prefix(
        self, request: ItemProcessingRequest
    ) -> ReplayPrefix:
        if request.replay_from_run_id is None:
            raise ValueError("replay source run is required")
        stages = PROCESSING_STAGE_ORDER[
            : PROCESSING_STAGE_ORDER.index(request.restart_from_stage)
        ]
        with self._session_factory() as db:
            records = list(
                db.scalars(
                    select(ProcessingCheckpoint)
                    .where(
                        ProcessingCheckpoint.processing_run_id
                        == request.replay_from_run_id,
                        ProcessingCheckpoint.invalidated_at.is_(None),
                    )
                    .order_by(ProcessingCheckpoint.id)
                )
            )
        by_stage = {record.stage: record for record in records}
        state: dict[str, Any] = {"review_decisions": {}}
        checkpoint_ids: dict[str, int] = {}
        for stage in stages:
            try:
                record = by_stage[stage.value]
            except KeyError as exc:
                raise ValueError(f"source run is missing {stage.value} checkpoint") from exc
            if record.graph_version != request.graph_version:
                raise ValueError("source checkpoint graph version does not match")
            if record.state_version != request.state_version:
                raise ValueError("source checkpoint state version does not match")
            state[stage.value] = dict(record.output_snapshot)
            checkpoint_ids[stage.value] = record.id
            decision = record.artifact_references.get("review_decision")
            if decision is not None:
                state["review_decisions"][stage.value] = ReviewDecision.model_validate(
                    decision
                ).model_dump(mode="json")
        return ReplayPrefix(
            source_run_id=request.replay_from_run_id,
            restart_from_stage=request.restart_from_stage,
            state=state,
            checkpoint_ids=checkpoint_ids,
        )

    async def publish(
        self,
        request: ItemProcessingRequest,
        evidence: EvidenceSnapshot,
        relevance: RelevanceProposal,
        media: MediaProposal,
        translation: TranslationProposal,
        analysis: MessageAnalysisProposal,
        importance: ImportanceProposal,
        evidence_gate: EvidenceGateProposal,
    ) -> PublicationResult:
        if request.run_mode != RunMode.PRODUCTION:
            raise RuntimeError("compatibility backend cannot publish non-production runs")
        with self._session_factory() as db:
            existing = db.scalar(
                select(NormalizedItemRevision).where(
                    NormalizedItemRevision.processing_run_id == request.workflow_run_id
                )
            )
            if existing is not None:
                return PublicationResult(
                    normalized_item_id=existing.normalized_item_id,
                    normalized_item_revision=existing.revision,
                )
            run = db.get(ProcessingRun, request.workflow_run_id)
            raw_item = _load_raw(db, evidence.raw_item_id)
            if run is None or run.raw_item_id != raw_item.id:
                raise ValueError("processing run does not own raw item")
            if not is_latest_raw_item(db, raw_item):
                raise ValueError("raw item has been superseded by a newer revision")
            item = _apply_normalized_item(
                db,
                raw_item,
                _build_item_proposal(
                    raw_item=raw_item,
                    translation_proposal=_translation_payload(translation),
                    analysis_proposal=_analysis_payload(analysis),
                    importance_proposal=_importance_payload(importance),
                    relevance_proposal=relevance.model_dump(mode="json"),
                    evidence_gate=evidence_gate.model_dump(mode="json"),
                    knowledge_snapshot=analysis.knowledge_rules,
                    ocr_corrections=media.ocr_corrections,
                ),
                processing_run_id=run.id,
            )
            enqueue_pipeline_job(
                db, raw_item_id=raw_item.id, current_stage="event_aggregation"
            )
            run.status = "completed"
            run.outcome = "approved"
            run.current_stage = ProcessingStage.PUBLICATION.value
            db.commit()
            db.refresh(item)
            return PublicationResult(
                normalized_item_id=item.id,
                normalized_item_revision=item.current_revision,
            )


def create_item_processing_run(
    db: Session,
    *,
    raw_item_id: int,
    review_mode: ReviewMode = ReviewMode.AUTOMATIC,
) -> ItemProcessingRequest:
    raw_item = _load_raw(db, raw_item_id)
    if not is_latest_raw_item(db, raw_item):
        raise ValueError("raw item has been superseded by a newer revision")
    if raw_item.normalized_item is not None:
        raise ValueError("raw item already has a normalized item")
    run = ProcessingRun(
        raw_item_id=raw_item.id,
        workflow_type="item",
        status="running",
        current_stage=ProcessingStage.EVIDENCE.value,
        execution_mode=review_mode.value,
        graph_name=ITEM_PROCESSING_GRAPH,
        graph_version=ITEM_PROCESSING_GRAPH_VERSION,
        state_version=ITEM_PROCESSING_STATE_VERSION,
        context={},
    )
    db.add(run)
    db.flush()
    request = ItemProcessingRequest(
        workflow_run_id=run.id,
        raw_item_id=raw_item.id,
        raw_item_revision=raw_item.revision,
        run_mode=RunMode.PRODUCTION,
        review_mode=review_mode,
    )
    run.thread_id = request.thread_id
    db.commit()
    return request
