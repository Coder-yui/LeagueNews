import asyncio
import hashlib

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.orchestration.contracts import (
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
    RunMode,
    ReviewMode,
    TranslationProposal,
)
from app.orchestration.item_processing import build_item_processing_graph


class FakeBackend:
    def __init__(
        self,
        *,
        relevance: str = "relevant",
        evidence_decision: str = "accept",
        manual_review: bool = False,
        fail_importance_once: bool = False,
    ) -> None:
        self.relevance_decision = relevance
        self.evidence_decision = evidence_decision
        self.manual_review = manual_review
        self.fail_importance_once = fail_importance_once
        self.calls: list[str] = []
        self.checkpoints: list[BusinessStageCheckpoint] = []
        self.replay_prefix: ReplayPrefix | None = None
        self.importance_titles: list[str] = []

    async def complete(
        self, _request: ItemProcessingRequest, outcome: str
    ) -> None:
        self.calls.append(f"complete_{outcome}")

    async def restore_replay_prefix(
        self, _request: ItemProcessingRequest
    ) -> ReplayPrefix:
        self.calls.append("restore_replay_prefix")
        if self.replay_prefix is None:
            raise RuntimeError("no replay fixture configured")
        return self.replay_prefix

    async def save_checkpoint(
        self,
        request: ItemProcessingRequest,
        checkpoint: BusinessStageCheckpoint,
    ) -> CheckpointReceipt:
        self.checkpoints.append(checkpoint)
        return CheckpointReceipt(
            checkpoint_id=len(self.checkpoints),
            workflow_run_id=request.workflow_run_id,
            stage=checkpoint.stage,
        )

    async def load_evidence(self, request: ItemProcessingRequest) -> EvidenceSnapshot:
        self.calls.append("load_evidence")
        source = f"{request.raw_item_id}:{request.raw_item_revision}"
        fingerprint = hashlib.sha256(source.encode()).hexdigest()
        return EvidenceSnapshot(
            raw_item_id=request.raw_item_id,
            raw_item_revision=request.raw_item_revision,
            title="Patch preview",
            language="en",
            text="Champion changes",
            evidence_fingerprint=fingerprint,
        )

    async def judge_relevance(self, _evidence: EvidenceSnapshot) -> RelevanceProposal:
        self.calls.append("relevance")
        return RelevanceProposal(
            decision=self.relevance_decision,
            confidence=0.99,
            reason="fixture",
        )

    async def understand_media(self, _evidence: EvidenceSnapshot) -> MediaProposal:
        self.calls.append("media")
        return MediaProposal()

    async def translate(
        self, _evidence: EvidenceSnapshot, _media: MediaProposal
    ) -> TranslationProposal:
        self.calls.append("translation")
        return TranslationProposal(
            status="translated",
            translated_title="版本前瞻",
            translated_text="英雄改动",
        )

    async def analyze_message(
        self,
        _evidence: EvidenceSnapshot,
        _media: MediaProposal,
        _translation: TranslationProposal,
    ) -> MessageAnalysisProposal:
        self.calls.append("message_analysis")
        return MessageAnalysisProposal(
            title="版本前瞻",
            summary="英雄改动",
            products=["lol_pc"],
            content_form="original",
            requires_manual_review=self.manual_review,
        )

    async def score_importance(
        self,
        _evidence: EvidenceSnapshot,
        _translation: TranslationProposal,
        _analysis: MessageAnalysisProposal,
    ) -> ImportanceProposal:
        self.calls.append("importance")
        self.importance_titles.append(_analysis.title)
        if self.fail_importance_once:
            self.fail_importance_once = False
            raise RuntimeError("temporary model failure")
        return ImportanceProposal(
            message_type="preview",
            topics=["balance_gameplay"],
            importance_score=0.7,
        )

    async def evaluate_evidence(
        self,
        _evidence: EvidenceSnapshot,
        _analysis: MessageAnalysisProposal,
        _importance: ImportanceProposal,
    ) -> EvidenceGateProposal:
        self.calls.append("evidence_gate")
        return EvidenceGateProposal(decision=self.evidence_decision)

    async def publish(self, *_args: object) -> PublicationResult:
        self.calls.append("publish")
        return PublicationResult(normalized_item_id=42, normalized_item_revision=1)


def _invoke(backend: FakeBackend, request: ItemProcessingRequest):
    graph = build_item_processing_graph(backend)
    return asyncio.run(
        graph.ainvoke({"request": request.model_dump(mode="json"), "trace": []})
    )


def test_production_run_publishes_after_all_stages() -> None:
    backend = FakeBackend()
    result = _invoke(
        backend,
        ItemProcessingRequest(
            workflow_run_id=1,
            raw_item_id=7,
            raw_item_revision=1,
            run_mode=RunMode.PRODUCTION,
        ),
    )

    assert result["outcome"] == "published"
    assert result["publication"] == {
        "normalized_item_id": 42,
        "normalized_item_revision": 1,
    }
    assert result["trace"] == [
        "bootstrap_fresh",
        "evidence",
        "checkpoint_evidence",
        "relevance",
        "review_relevance_automatic",
        "checkpoint_relevance",
        "media",
        "review_media_automatic",
        "checkpoint_media",
        "translation",
        "review_translation_automatic",
        "checkpoint_translation",
        "message_analysis",
        "review_message_analysis_automatic",
        "checkpoint_message_analysis",
        "importance",
        "review_importance_automatic",
        "checkpoint_importance",
        "evidence_gate",
        "review_evidence_gate_automatic",
        "checkpoint_evidence_gate",
        "publication_gate",
        "publication",
        "checkpoint_publication",
    ]
    assert [checkpoint.stage for checkpoint in backend.checkpoints] == list(
        ProcessingStage
    )


def test_irrelevant_run_stops_before_expensive_stages() -> None:
    backend = FakeBackend(relevance="irrelevant")
    result = _invoke(
        backend,
        ItemProcessingRequest(
            workflow_run_id=2,
            raw_item_id=8,
            raw_item_revision=1,
            run_mode=RunMode.PRODUCTION,
        ),
    )

    assert result["outcome"] == "irrelevant"
    assert backend.calls == ["load_evidence", "relevance", "complete_irrelevant"]


def test_experiment_run_can_never_publish() -> None:
    backend = FakeBackend()
    request = ItemProcessingRequest(
        workflow_run_id=3,
        raw_item_id=9,
        raw_item_revision=2,
        run_mode=RunMode.EXPERIMENT,
        batch_id=12,
    )
    result = _invoke(backend, request)

    assert result["outcome"] == "preview_completed"
    assert "publish" not in backend.calls
    assert request.thread_id == (
        "item_processing:v3.0.0-dev2:experiment:batch:12:run:3:raw:9:revision:2"
    )


def test_manual_review_interrupts_and_resumes_same_thread() -> None:
    backend = FakeBackend(manual_review=True)
    graph = build_item_processing_graph(backend, checkpointer=InMemorySaver())
    request = ItemProcessingRequest(
        workflow_run_id=4,
        raw_item_id=10,
        raw_item_revision=1,
        run_mode=RunMode.PRODUCTION,
    )
    config = {"configurable": {"thread_id": request.thread_id}}

    paused = asyncio.run(
        graph.ainvoke(
            {"request": request.model_dump(mode="json"), "trace": []},
            config=config,
        )
    )
    assert "__interrupt__" in paused
    assert paused["__interrupt__"][0].value["stage"] == "message_analysis"
    assert "importance" not in backend.calls
    assert "publish" not in backend.calls

    completed = asyncio.run(
        graph.ainvoke(
            Command(resume={"action": "approve", "note": "reviewed"}),
            config=config,
        )
    )
    assert completed["outcome"] == "published"
    assert completed["review_decisions"]["message_analysis"] == {
        "action": "approve",
        "note": "reviewed",
        "replacement": None,
    }
    assert backend.calls.count("message_analysis") == 1
    assert backend.calls.count("publish") == 1


def test_manual_replacement_is_validated_checkpointed_and_used_downstream() -> None:
    backend = FakeBackend(manual_review=True)
    graph = build_item_processing_graph(backend, checkpointer=InMemorySaver())
    request = ItemProcessingRequest(
        workflow_run_id=9,
        raw_item_id=14,
        raw_item_revision=1,
        run_mode=RunMode.PRODUCTION,
    )
    config = {"configurable": {"thread_id": request.thread_id}}
    asyncio.run(
        graph.ainvoke(
            {"request": request.model_dump(mode="json"), "trace": []},
            config=config,
        )
    )
    replacement = MessageAnalysisProposal(
        title="人工修正标题",
        summary="人工修正摘要",
        products=["lol_pc"],
        content_form="original",
    )

    completed = asyncio.run(
        graph.ainvoke(
            Command(
                resume={
                    "action": "approve",
                    "note": "replace model result",
                    "replacement": replacement.model_dump(mode="json"),
                }
            ),
            config=config,
        )
    )

    assert completed["outcome"] == "published"
    assert backend.importance_titles == ["人工修正标题"]
    analysis_checkpoint = next(
        checkpoint
        for checkpoint in backend.checkpoints
        if checkpoint.stage == ProcessingStage.MESSAGE_ANALYSIS
    )
    assert analysis_checkpoint.output_snapshot["title"] == "人工修正标题"


def test_manual_mode_interrupts_every_reviewable_stage() -> None:
    backend = FakeBackend()
    graph = build_item_processing_graph(backend, checkpointer=InMemorySaver())
    request = ItemProcessingRequest(
        workflow_run_id=5,
        raw_item_id=11,
        raw_item_revision=1,
        run_mode=RunMode.PRODUCTION,
        review_mode=ReviewMode.MANUAL,
    )
    paused = asyncio.run(
        graph.ainvoke(
            {"request": request.model_dump(mode="json"), "trace": []},
            config={"configurable": {"thread_id": request.thread_id}},
        )
    )

    assert paused["__interrupt__"][0].value["stage"] == "relevance"
    assert [checkpoint.stage for checkpoint in backend.checkpoints] == [
        ProcessingStage.EVIDENCE
    ]


def test_replay_from_translation_reuses_only_approved_prefix() -> None:
    backend = FakeBackend()
    original = ItemProcessingRequest(
        workflow_run_id=6,
        raw_item_id=12,
        raw_item_revision=1,
        run_mode=RunMode.PRODUCTION,
    )
    original_result = _invoke(backend, original)
    original_checkpoint_ids = original_result["checkpoint_ids"]
    backend.calls.clear()
    backend.checkpoints.clear()
    backend.replay_prefix = ReplayPrefix(
        source_run_id=original.workflow_run_id,
        restart_from_stage=ProcessingStage.TRANSLATION,
        state={
            "evidence": original_result["evidence"],
            "relevance": original_result["relevance"],
            "media": original_result["media"],
            "review_decisions": {
                key: original_result["review_decisions"][key]
                for key in ("relevance", "media")
            },
        },
        checkpoint_ids={
            key: original_checkpoint_ids[key]
            for key in ("evidence", "relevance", "media")
        },
    )
    replay = ItemProcessingRequest(
        workflow_run_id=7,
        replay_from_run_id=6,
        restart_from_stage=ProcessingStage.TRANSLATION,
        raw_item_id=12,
        raw_item_revision=1,
        run_mode=RunMode.PRODUCTION,
    )

    replay_result = _invoke(backend, replay)

    assert replay_result["outcome"] == "published"
    assert backend.calls == [
        "restore_replay_prefix",
        "translation",
        "message_analysis",
        "importance",
        "evidence_gate",
        "publish",
    ]
    assert [checkpoint.stage for checkpoint in backend.checkpoints] == [
        ProcessingStage.TRANSLATION,
        ProcessingStage.MESSAGE_ANALYSIS,
        ProcessingStage.IMPORTANCE,
        ProcessingStage.EVIDENCE_GATE,
        ProcessingStage.PUBLICATION,
    ]


def test_failure_resumes_same_run_at_failed_stage() -> None:
    backend = FakeBackend(fail_importance_once=True)
    graph = build_item_processing_graph(backend, checkpointer=InMemorySaver())
    request = ItemProcessingRequest(
        workflow_run_id=8,
        raw_item_id=13,
        raw_item_revision=1,
        run_mode=RunMode.PRODUCTION,
    )
    config = {"configurable": {"thread_id": request.thread_id}}

    try:
        asyncio.run(
            graph.ainvoke(
                {"request": request.model_dump(mode="json"), "trace": []},
                config=config,
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "temporary model failure"
    else:
        raise AssertionError("the first importance attempt must fail")

    completed = asyncio.run(graph.ainvoke(None, config=config))

    assert completed["outcome"] == "published"
    assert backend.calls.count("load_evidence") == 1
    assert backend.calls.count("translation") == 1
    assert backend.calls.count("message_analysis") == 1
    assert backend.calls.count("importance") == 2
