"""Exercise canonical publication from an already-reviewed fixture proposal."""

from sqlalchemy.orm import sessionmaker
from app.methods import MethodAssembly
from app.orchestration.contracts import (
    ItemProcessingRequest,
    RunMode,
    RelevanceProposal,
    MediaProposal,
    TranslationProposal,
    MessageAnalysisProposal,
    ImportanceProposal,
    EvidenceGateProposal,
)
from app.orchestration.item_processing.backend import ItemProcessingBackendV3
from app.models.workflow import ProcessingRun


async def publish_reviewed_fixture(db, review, *, note=None):
    run = review.processing_run
    run_id = run.id
    request = ItemProcessingRequest(
        workflow_run_id=run.id,
        raw_item_id=run.raw_item_id,
        raw_item_revision=run.raw_item.revision,
        run_mode=RunMode.PRODUCTION,
    )
    translation = dict(run.context["approved_translation_proposal"])
    translation["status"] = translation.pop("translation_status")
    translation.pop("approved_media_extraction_ids", None)
    analysis = dict(run.context["approved_message_analysis_proposal"])
    importance = dict(review.proposal)
    importance["calculation"] = importance.pop("importance_calculation", {})
    review.status = "approved"
    db.commit()
    backend = ItemProcessingBackendV3(
        sessionmaker(db.bind, expire_on_commit=False),
        method_assembly=MethodAssembly(),
    )
    evidence = await backend.load_evidence(request)
    await backend.publish(
        request,
        evidence,
        RelevanceProposal(decision="relevant", confidence=1, reason="reviewed fixture"),
        MediaProposal(),
        TranslationProposal.model_validate(translation),
        MessageAnalysisProposal.model_validate(analysis),
        ImportanceProposal.model_validate(importance),
        EvidenceGateProposal(decision="accept", reason="reviewed fixture"),
    )
    db.expire_all()
    return db.get(ProcessingRun, run_id)
