import operator
from collections.abc import Callable
from typing import Annotated, Any, Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.orchestration.contracts import (
    PROCESSING_STAGE_ORDER,
    PROCESSING_STAGE_OUTPUT_KEYS,
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


PROPOSAL_TYPES: dict[ProcessingStage, type[Any]] = {
    ProcessingStage.EVIDENCE: EvidenceSnapshot,
    ProcessingStage.RELEVANCE: RelevanceProposal,
    ProcessingStage.MEDIA: MediaProposal,
    ProcessingStage.TRANSLATION: TranslationProposal,
    ProcessingStage.MESSAGE_ANALYSIS: MessageAnalysisProposal,
    ProcessingStage.IMPORTANCE: ImportanceProposal,
    ProcessingStage.EVIDENCE_GATE: EvidenceGateProposal,
    ProcessingStage.PUBLICATION: PublicationResult,
}
REVIEWED_STAGES = frozenset(
    {
        ProcessingStage.RELEVANCE,
        ProcessingStage.MEDIA,
        ProcessingStage.TRANSLATION,
        ProcessingStage.MESSAGE_ANALYSIS,
        ProcessingStage.IMPORTANCE,
        ProcessingStage.EVIDENCE_GATE,
    }
)


class ItemProcessingState(TypedDict, total=False):
    request: dict[str, object]
    evidence: dict[str, object]
    relevance: dict[str, object]
    media: dict[str, object]
    translation: dict[str, object]
    message_analysis: dict[str, object]
    importance: dict[str, object]
    evidence_gate: dict[str, object]
    review_decisions: dict[str, dict[str, object]]
    checkpoint_ids: dict[str, int]
    publication: dict[str, object]
    outcome: str
    trace: Annotated[list[str], operator.add]


class ItemProcessingBackend(Protocol):
    """Domain boundary for the v3 item graph.

    Checkpoint writes must be idempotent on ``(workflow_run_id, stage)``.
    Graph state stays JSON-serializable and database transactions may not cross
    an await to a model, OCR provider, or other remote dependency.
    """

    async def load_evidence(self, request: ItemProcessingRequest) -> EvidenceSnapshot: ...

    async def restore_replay_prefix(self, request: ItemProcessingRequest) -> ReplayPrefix: ...

    async def save_checkpoint(
        self,
        request: ItemProcessingRequest,
        checkpoint: BusinessStageCheckpoint,
    ) -> CheckpointReceipt: ...

    async def judge_relevance(self, evidence: EvidenceSnapshot) -> RelevanceProposal: ...

    async def understand_media(self, evidence: EvidenceSnapshot) -> MediaProposal: ...

    async def translate(
        self, evidence: EvidenceSnapshot, media: MediaProposal
    ) -> TranslationProposal: ...

    async def analyze_message(
        self,
        evidence: EvidenceSnapshot,
        media: MediaProposal,
        translation: TranslationProposal,
    ) -> MessageAnalysisProposal: ...

    async def score_importance(
        self,
        evidence: EvidenceSnapshot,
        translation: TranslationProposal,
        analysis: MessageAnalysisProposal,
    ) -> ImportanceProposal: ...

    async def evaluate_evidence(
        self,
        evidence: EvidenceSnapshot,
        analysis: MessageAnalysisProposal,
        importance: ImportanceProposal,
    ) -> EvidenceGateProposal: ...

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
    ) -> PublicationResult: ...

    async def complete(
        self, request: ItemProcessingRequest, outcome: str
    ) -> None: ...


def _model(state: ItemProcessingState, key: str, model_type: type[Any]) -> Any:
    return model_type.model_validate(state[key])


def _stages_before(stage: ProcessingStage) -> tuple[ProcessingStage, ...]:
    return PROCESSING_STAGE_ORDER[: PROCESSING_STAGE_ORDER.index(stage)]


def _validated_replay_state(
    request: ItemProcessingRequest,
    replay: ReplayPrefix,
) -> ItemProcessingState:
    if replay.source_run_id != request.replay_from_run_id:
        raise ValueError("replay prefix belongs to another source run")
    if replay.restart_from_stage != request.restart_from_stage:
        raise ValueError("replay prefix targets another restart stage")

    restored: ItemProcessingState = {
        "checkpoint_ids": {},
        "review_decisions": {},
    }
    review_decisions = dict(replay.state.get("review_decisions") or {})
    for stage in _stages_before(request.restart_from_stage):
        key = PROCESSING_STAGE_OUTPUT_KEYS[stage]
        if key not in replay.state:
            raise ValueError(f"replay prefix is missing {stage} output")
        if stage.value not in replay.checkpoint_ids:
            raise ValueError(f"replay prefix is missing {stage} checkpoint")
        restored[key] = PROPOSAL_TYPES[stage].model_validate(
            replay.state[key]
        ).model_dump(mode="json")
        restored["checkpoint_ids"][stage.value] = replay.checkpoint_ids[stage.value]
        if stage in REVIEWED_STAGES:
            if stage.value not in review_decisions:
                raise ValueError(f"replay prefix is missing {stage} review decision")
            restored["review_decisions"][stage.value] = ReviewDecision.model_validate(
                review_decisions[stage.value]
            ).model_dump(mode="json")

    evidence = EvidenceSnapshot.model_validate(restored["evidence"])
    if evidence.raw_item_id != request.raw_item_id:
        raise ValueError("replay evidence belongs to another raw item")
    if evidence.raw_item_revision != request.raw_item_revision:
        raise ValueError("replay evidence belongs to another raw revision")
    return restored


def build_item_processing_graph(
    backend: ItemProcessingBackend,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
):
    async def bootstrap(state: ItemProcessingState) -> ItemProcessingState:
        request = ItemProcessingRequest.model_validate(state["request"])
        if request.restart_from_stage == ProcessingStage.EVIDENCE:
            return {
                "checkpoint_ids": {},
                "review_decisions": {},
                "trace": ["bootstrap_fresh"],
            }
        replay = await backend.restore_replay_prefix(request)
        restored = _validated_replay_state(request, replay)
        return {
            **restored,
            "trace": [
                f"bootstrap_replay_{replay.source_run_id}_from_"
                f"{request.restart_from_stage.value}"
            ],
        }

    def route_entry(state: ItemProcessingState) -> str:
        request = ItemProcessingRequest.model_validate(state["request"])
        return request.restart_from_stage.value

    async def load_evidence(state: ItemProcessingState) -> ItemProcessingState:
        request = ItemProcessingRequest.model_validate(state["request"])
        evidence = await backend.load_evidence(request)
        if evidence.raw_item_id != request.raw_item_id:
            raise ValueError("loaded evidence belongs to another raw item")
        if evidence.raw_item_revision != request.raw_item_revision:
            raise ValueError("loaded evidence revision does not match graph request")
        return {"evidence": evidence.model_dump(mode="json"), "trace": ["evidence"]}

    async def relevance(state: ItemProcessingState) -> ItemProcessingState:
        proposal = await backend.judge_relevance(
            _model(state, "evidence", EvidenceSnapshot)
        )
        return {"relevance": proposal.model_dump(mode="json"), "trace": ["relevance"]}

    def route_after_relevance(state: ItemProcessingState) -> str:
        decision = ReviewDecision.model_validate(state["review_decisions"]["relevance"])
        if decision.action == "reject":
            return "rejected"
        proposal = _model(state, "relevance", RelevanceProposal)
        return "irrelevant" if proposal.decision == "irrelevant" else "continue"

    async def media(state: ItemProcessingState) -> ItemProcessingState:
        proposal = await backend.understand_media(
            _model(state, "evidence", EvidenceSnapshot)
        )
        return {"media": proposal.model_dump(mode="json"), "trace": ["media"]}

    async def translation(state: ItemProcessingState) -> ItemProcessingState:
        proposal = await backend.translate(
            _model(state, "evidence", EvidenceSnapshot),
            _model(state, "media", MediaProposal),
        )
        return {"translation": proposal.model_dump(mode="json"), "trace": ["translation"]}

    async def message_analysis(state: ItemProcessingState) -> ItemProcessingState:
        proposal = await backend.analyze_message(
            _model(state, "evidence", EvidenceSnapshot),
            _model(state, "media", MediaProposal),
            _model(state, "translation", TranslationProposal),
        )
        return {
            "message_analysis": proposal.model_dump(mode="json"),
            "trace": ["message_analysis"],
        }

    async def importance(state: ItemProcessingState) -> ItemProcessingState:
        proposal = await backend.score_importance(
            _model(state, "evidence", EvidenceSnapshot),
            _model(state, "translation", TranslationProposal),
            _model(state, "message_analysis", MessageAnalysisProposal),
        )
        return {"importance": proposal.model_dump(mode="json"), "trace": ["importance"]}

    async def evidence_gate(state: ItemProcessingState) -> ItemProcessingState:
        proposal = await backend.evaluate_evidence(
            _model(state, "evidence", EvidenceSnapshot),
            _model(state, "message_analysis", MessageAnalysisProposal),
            _model(state, "importance", ImportanceProposal),
        )
        return {
            "evidence_gate": proposal.model_dump(mode="json"),
            "trace": ["evidence_gate"],
        }

    def route_after_evidence(state: ItemProcessingState) -> str:
        decision = ReviewDecision.model_validate(
            state["review_decisions"]["evidence_gate"]
        )
        if decision.action == "reject":
            return "rejected"
        proposal = _model(state, "evidence_gate", EvidenceGateProposal)
        return "insufficient" if proposal.decision == "reject" else "continue"

    def review_node(
        stage: ProcessingStage,
        proposal_type: type[Any],
    ) -> Callable[[ItemProcessingState], ItemProcessingState]:
        def node(state: ItemProcessingState) -> ItemProcessingState:
            request = ItemProcessingRequest.model_validate(state["request"])
            proposal = _model(state, stage.value, proposal_type)
            needs_review = (
                request.review_mode == ReviewMode.MANUAL
                or proposal.requires_manual_review
            )
            if needs_review:
                decision = ReviewDecision.model_validate(
                    interrupt(
                        {
                            "kind": "item_processing_review",
                            "stage": stage.value,
                            "review_mode": request.review_mode,
                            "request": state["request"],
                            "proposal": state[stage.value],
                        }
                    )
                )
                source = "manual"
            else:
                decision = ReviewDecision(
                    action="approve",
                    note="automatic policy approval",
                )
                source = "automatic"

            replacement: dict[str, Any] | None = None
            if decision.replacement is not None:
                replacement = proposal_type.model_validate(
                    decision.replacement
                ).model_dump(mode="json")
            decisions = dict(state.get("review_decisions", {}))
            decisions[stage.value] = decision.model_dump(mode="json")
            result: ItemProcessingState = {
                "review_decisions": decisions,
                "trace": [f"review_{stage.value}_{source}"],
            }
            if replacement is not None:
                result[stage.value] = replacement
            return result

        return node

    def route_review(stage: ProcessingStage) -> Callable[[ItemProcessingState], str]:
        def route(state: ItemProcessingState) -> str:
            decision = ReviewDecision.model_validate(
                state["review_decisions"][stage.value]
            )
            return "continue" if decision.action == "approve" else "rejected"

        return route

    def checkpoint_node(
        stage: ProcessingStage,
    ) -> Callable[[ItemProcessingState], Any]:
        async def node(state: ItemProcessingState) -> ItemProcessingState:
            request = ItemProcessingRequest.model_validate(state["request"])
            key = PROCESSING_STAGE_OUTPUT_KEYS[stage]
            output = PROPOSAL_TYPES[stage].model_validate(state[key]).model_dump(mode="json")
            evidence = _model(state, "evidence", EvidenceSnapshot)
            decision = None
            if stage in REVIEWED_STAGES:
                decision = ReviewDecision.model_validate(
                    state["review_decisions"][stage.value]
                )
            checkpoint_ids = dict(state.get("checkpoint_ids", {}))
            receipt = await backend.save_checkpoint(
                request,
                BusinessStageCheckpoint(
                    workflow_run_id=request.workflow_run_id,
                    stage=stage,
                    output_snapshot=output,
                    review_decision=decision,
                    evidence_fingerprint=evidence.evidence_fingerprint,
                    upstream_checkpoint_ids=checkpoint_ids,
                    graph_version=request.graph_version,
                    state_version=request.state_version,
                ),
            )
            if receipt.workflow_run_id != request.workflow_run_id or receipt.stage != stage:
                raise ValueError("backend returned a checkpoint receipt for another stage")
            checkpoint_ids[stage.value] = receipt.checkpoint_id
            return {
                "checkpoint_ids": checkpoint_ids,
                "trace": [f"checkpoint_{stage.value}"],
            }

        return node

    async def publish(state: ItemProcessingState) -> ItemProcessingState:
        request = ItemProcessingRequest.model_validate(state["request"])
        if request.run_mode != RunMode.PRODUCTION:
            raise RuntimeError("non-production graph attempted to publish")
        result = await backend.publish(
            request,
            _model(state, "evidence", EvidenceSnapshot),
            _model(state, "relevance", RelevanceProposal),
            _model(state, "media", MediaProposal),
            _model(state, "translation", TranslationProposal),
            _model(state, "message_analysis", MessageAnalysisProposal),
            _model(state, "importance", ImportanceProposal),
            _model(state, "evidence_gate", EvidenceGateProposal),
        )
        return {
            "publication": result.model_dump(mode="json"),
            "outcome": "published",
            "trace": ["publication"],
        }

    def route_publication(state: ItemProcessingState) -> str:
        request = ItemProcessingRequest.model_validate(state["request"])
        return "publish" if request.run_mode == RunMode.PRODUCTION else "preview"

    def complete(
        outcome: str, trace: str
    ) -> Callable[[ItemProcessingState], Any]:
        async def node(state: ItemProcessingState) -> ItemProcessingState:
            request = ItemProcessingRequest.model_validate(state["request"])
            await backend.complete(request, outcome)
            return {"outcome": outcome, "trace": [trace]}

        return node

    builder = StateGraph(ItemProcessingState)
    builder.add_node("bootstrap", bootstrap)
    builder.add_node("evidence", load_evidence)
    builder.add_node("checkpoint_evidence", checkpoint_node(ProcessingStage.EVIDENCE))
    builder.add_node("relevance", relevance)
    builder.add_node(
        "review_relevance", review_node(ProcessingStage.RELEVANCE, RelevanceProposal)
    )
    builder.add_node("checkpoint_relevance", checkpoint_node(ProcessingStage.RELEVANCE))
    builder.add_node("complete_irrelevant", complete("irrelevant", "complete_irrelevant"))
    builder.add_node("media", media)
    builder.add_node("review_media", review_node(ProcessingStage.MEDIA, MediaProposal))
    builder.add_node("checkpoint_media", checkpoint_node(ProcessingStage.MEDIA))
    builder.add_node("translation", translation)
    builder.add_node(
        "review_translation", review_node(ProcessingStage.TRANSLATION, TranslationProposal)
    )
    builder.add_node("checkpoint_translation", checkpoint_node(ProcessingStage.TRANSLATION))
    builder.add_node("message_analysis", message_analysis)
    builder.add_node(
        "review_message_analysis",
        review_node(ProcessingStage.MESSAGE_ANALYSIS, MessageAnalysisProposal),
    )
    builder.add_node(
        "checkpoint_message_analysis", checkpoint_node(ProcessingStage.MESSAGE_ANALYSIS)
    )
    builder.add_node("importance", importance)
    builder.add_node(
        "review_importance", review_node(ProcessingStage.IMPORTANCE, ImportanceProposal)
    )
    builder.add_node("checkpoint_importance", checkpoint_node(ProcessingStage.IMPORTANCE))
    builder.add_node("evidence_gate", evidence_gate)
    builder.add_node(
        "review_evidence_gate",
        review_node(ProcessingStage.EVIDENCE_GATE, EvidenceGateProposal),
    )
    builder.add_node(
        "checkpoint_evidence_gate", checkpoint_node(ProcessingStage.EVIDENCE_GATE)
    )
    builder.add_node(
        "complete_insufficient", complete("insufficient_evidence", "complete_insufficient")
    )
    builder.add_node("complete_rejected", complete("review_rejected", "complete_rejected"))
    builder.add_node("publication", lambda _state: {"trace": ["publication_gate"]})
    builder.add_node("complete_preview", complete("preview_completed", "complete_preview"))
    builder.add_node("publish", publish)
    builder.add_node(
        "checkpoint_publication", checkpoint_node(ProcessingStage.PUBLICATION)
    )

    builder.add_edge(START, "bootstrap")
    builder.add_conditional_edges(
        "bootstrap",
        route_entry,
        {stage.value: stage.value for stage in PROCESSING_STAGE_ORDER},
    )
    builder.add_edge("evidence", "checkpoint_evidence")
    builder.add_edge("checkpoint_evidence", "relevance")
    builder.add_edge("relevance", "review_relevance")
    builder.add_edge("review_relevance", "checkpoint_relevance")
    builder.add_conditional_edges(
        "checkpoint_relevance",
        route_after_relevance,
        {
            "rejected": "complete_rejected",
            "irrelevant": "complete_irrelevant",
            "continue": "media",
        },
    )
    builder.add_edge("complete_irrelevant", END)
    builder.add_edge("media", "review_media")
    builder.add_edge("review_media", "checkpoint_media")
    builder.add_conditional_edges(
        "checkpoint_media",
        route_review(ProcessingStage.MEDIA),
        {"continue": "translation", "rejected": "complete_rejected"},
    )
    builder.add_edge("translation", "review_translation")
    builder.add_edge("review_translation", "checkpoint_translation")
    builder.add_conditional_edges(
        "checkpoint_translation",
        route_review(ProcessingStage.TRANSLATION),
        {"continue": "message_analysis", "rejected": "complete_rejected"},
    )
    builder.add_edge("message_analysis", "review_message_analysis")
    builder.add_edge("review_message_analysis", "checkpoint_message_analysis")
    builder.add_conditional_edges(
        "checkpoint_message_analysis",
        route_review(ProcessingStage.MESSAGE_ANALYSIS),
        {"continue": "importance", "rejected": "complete_rejected"},
    )
    builder.add_edge("importance", "review_importance")
    builder.add_edge("review_importance", "checkpoint_importance")
    builder.add_conditional_edges(
        "checkpoint_importance",
        route_review(ProcessingStage.IMPORTANCE),
        {"continue": "evidence_gate", "rejected": "complete_rejected"},
    )
    builder.add_edge("evidence_gate", "review_evidence_gate")
    builder.add_edge("review_evidence_gate", "checkpoint_evidence_gate")
    builder.add_conditional_edges(
        "checkpoint_evidence_gate",
        route_after_evidence,
        {
            "rejected": "complete_rejected",
            "insufficient": "complete_insufficient",
            "continue": "publication",
        },
    )
    builder.add_edge("complete_insufficient", END)
    builder.add_conditional_edges(
        "publication",
        route_publication,
        {"publish": "publish", "preview": "complete_preview"},
    )
    builder.add_edge("complete_rejected", END)
    builder.add_edge("complete_preview", END)
    builder.add_edge("publish", "checkpoint_publication")
    builder.add_edge("checkpoint_publication", END)
    return builder.compile(checkpointer=checkpointer)
