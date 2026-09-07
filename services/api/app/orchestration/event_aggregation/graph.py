import operator
from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.orchestration.contracts import RunMode
from app.schemas.event_aggregation import EventAggregationResult


EVENT_AGGREGATION_GRAPH = "event_aggregation"
EVENT_AGGREGATION_GRAPH_VERSION = "v3.0.0-dev2"
EVENT_AGGREGATION_STATE_VERSION = 1


class EventAggregationStage(StrEnum):
    LOAD_MESSAGE = "load_message"
    MINIMAL_FILTER = "minimal_filter"
    CANDIDATE_RETRIEVAL = "candidate_retrieval"
    SEMANTIC_DECISION = "semantic_decision"
    APPLY_MEMBERSHIP = "apply_membership"
    REFRESH_PROJECTION = "refresh_projection"


EVENT_AGGREGATION_STAGE_ORDER = tuple(EventAggregationStage)


class StrictGraphModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventAggregationRequest(StrictGraphModel):
    workflow_run_id: int = Field(ge=1)
    normalized_item_id: int = Field(ge=1)
    normalized_item_revision: int = Field(ge=1)
    run_mode: RunMode
    batch_id: int | None = Field(default=None, ge=1)
    graph_version: str = EVENT_AGGREGATION_GRAPH_VERSION
    state_version: int = EVENT_AGGREGATION_STATE_VERSION

    @model_validator(mode="after")
    def validate_scope(self) -> "EventAggregationRequest":
        if self.run_mode == RunMode.EXPERIMENT and self.batch_id is None:
            raise ValueError("experiment runs require batch_id")
        if self.run_mode != RunMode.EXPERIMENT and self.batch_id is not None:
            raise ValueError("batch_id is only valid for experiment runs")
        return self

    @property
    def thread_id(self) -> str:
        scope = f"batch:{self.batch_id}" if self.batch_id is not None else "live"
        return (
            f"{EVENT_AGGREGATION_GRAPH}:{self.graph_version}:{self.run_mode}:"
            f"{scope}:run:{self.workflow_run_id}:item:{self.normalized_item_id}:"
            f"revision:{self.normalized_item_revision}"
        )


class EventMessageSnapshot(StrictGraphModel):
    normalized_item_id: int = Field(ge=1)
    normalized_item_revision: int = Field(ge=1)
    message: dict[str, Any]
    input_truncation: dict[str, Any] = Field(default_factory=dict)
    evidence_fingerprint: str = Field(min_length=1)


class EventAdmissionProposal(StrictGraphModel):
    decision: Literal["process", "skip"]
    reasons: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    possible_event_families: list[str] = Field(default_factory=list)
    entity_hints: dict[str, Any] = Field(default_factory=dict)


class EventCandidateProposal(StrictGraphModel):
    candidates: list[dict[str, Any]] = Field(default_factory=list, max_length=24)


class EventDecisionProposal(StrictGraphModel):
    result: EventAggregationResult
    suppressed_mentions: list[dict[str, Any]] = Field(default_factory=list)
    execution_metadata: dict[str, Any] = Field(default_factory=dict)


class EventMembershipResult(StrictGraphModel):
    applied_count: int = Field(ge=0)
    affected_event_ids: list[int] = Field(default_factory=list)
    historical_event_ids: list[int] = Field(default_factory=list)


class EventProjectionResult(StrictGraphModel):
    refreshed_event_ids: list[int] = Field(default_factory=list)


class EventAggregationState(TypedDict, total=False):
    request: dict[str, object]
    message_snapshot: dict[str, object]
    admission: dict[str, object]
    candidate_retrieval: dict[str, object]
    semantic_decision: dict[str, object]
    membership: dict[str, object]
    projection: dict[str, object]
    outcome: str
    trace: Annotated[list[str], operator.add]


class EventAggregationBackend(Protocol):
    async def load_message(
        self, request: EventAggregationRequest
    ) -> EventMessageSnapshot: ...

    async def minimal_filter(
        self, request: EventAggregationRequest
    ) -> EventAdmissionProposal: ...

    async def retrieve_candidates(
        self,
        request: EventAggregationRequest,
        admission: EventAdmissionProposal,
    ) -> EventCandidateProposal: ...

    async def decide_semantics(
        self,
        request: EventAggregationRequest,
        snapshot: EventMessageSnapshot,
        admission: EventAdmissionProposal,
        candidates: EventCandidateProposal,
    ) -> EventDecisionProposal: ...

    async def save_stage(
        self,
        request: EventAggregationRequest,
        stage: EventAggregationStage,
        output: dict[str, Any],
    ) -> None: ...

    async def apply_membership(
        self,
        request: EventAggregationRequest,
        candidates: EventCandidateProposal,
        decision: EventDecisionProposal,
    ) -> EventMembershipResult: ...

    async def refresh_projection(
        self,
        request: EventAggregationRequest,
        membership: EventMembershipResult,
    ) -> EventProjectionResult: ...

    async def complete(
        self, request: EventAggregationRequest, outcome: str
    ) -> None: ...


def build_event_aggregation_graph(
    backend: EventAggregationBackend,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
):
    async def load_message(state: EventAggregationState) -> EventAggregationState:
        request = EventAggregationRequest.model_validate(state["request"])
        value = await backend.load_message(request)
        await backend.save_stage(
            request, EventAggregationStage.LOAD_MESSAGE, value.model_dump(mode="json")
        )
        return {"message_snapshot": value.model_dump(mode="json"), "trace": ["load_message"]}

    async def minimal_filter_node(state: EventAggregationState) -> EventAggregationState:
        request = EventAggregationRequest.model_validate(state["request"])
        value = await backend.minimal_filter(request)
        await backend.save_stage(
            request, EventAggregationStage.MINIMAL_FILTER, value.model_dump(mode="json")
        )
        return {"admission": value.model_dump(mode="json"), "trace": ["minimal_filter"]}

    def route_admission(state: EventAggregationState) -> str:
        return EventAdmissionProposal.model_validate(state["admission"]).decision

    async def candidates_node(state: EventAggregationState) -> EventAggregationState:
        request = EventAggregationRequest.model_validate(state["request"])
        value = await backend.retrieve_candidates(
            request, EventAdmissionProposal.model_validate(state["admission"])
        )
        await backend.save_stage(
            request,
            EventAggregationStage.CANDIDATE_RETRIEVAL,
            value.model_dump(mode="json"),
        )
        return {"candidate_retrieval": value.model_dump(mode="json"), "trace": ["candidate_retrieval"]}

    async def decision_node(state: EventAggregationState) -> EventAggregationState:
        request = EventAggregationRequest.model_validate(state["request"])
        value = await backend.decide_semantics(
            request,
            EventMessageSnapshot.model_validate(state["message_snapshot"]),
            EventAdmissionProposal.model_validate(state["admission"]),
            EventCandidateProposal.model_validate(state["candidate_retrieval"]),
        )
        return {"semantic_decision": value.model_dump(mode="json"), "trace": ["semantic_decision"]}

    async def checkpoint_decision(state: EventAggregationState) -> EventAggregationState:
        request = EventAggregationRequest.model_validate(state["request"])
        output = dict(state["semantic_decision"])
        await backend.save_stage(request, EventAggregationStage.SEMANTIC_DECISION, output)
        return {"trace": ["checkpoint_semantic_decision"]}

    def route_after_decision(state: EventAggregationState) -> str:
        request = EventAggregationRequest.model_validate(state["request"])
        return "apply" if request.run_mode == RunMode.PRODUCTION else "preview"

    async def membership_node(state: EventAggregationState) -> EventAggregationState:
        request = EventAggregationRequest.model_validate(state["request"])
        value = await backend.apply_membership(
            request,
            EventCandidateProposal.model_validate(state["candidate_retrieval"]),
            EventDecisionProposal.model_validate(state["semantic_decision"]),
        )
        await backend.save_stage(
            request, EventAggregationStage.APPLY_MEMBERSHIP, value.model_dump(mode="json")
        )
        return {"membership": value.model_dump(mode="json"), "trace": ["apply_membership"]}

    async def projection_node(state: EventAggregationState) -> EventAggregationState:
        request = EventAggregationRequest.model_validate(state["request"])
        value = await backend.refresh_projection(
            request, EventMembershipResult.model_validate(state["membership"])
        )
        await backend.save_stage(
            request, EventAggregationStage.REFRESH_PROJECTION, value.model_dump(mode="json")
        )
        outcome = (
            "applied"
            if EventMembershipResult.model_validate(state["membership"]).applied_count
            else "ignored"
        )
        await backend.complete(request, outcome)
        return {
            "projection": value.model_dump(mode="json"),
            "outcome": outcome,
            "trace": ["refresh_projection"],
        }

    def terminal(outcome: str):
        async def node(state: EventAggregationState) -> EventAggregationState:
            request = EventAggregationRequest.model_validate(state["request"])
            await backend.complete(request, outcome)
            return {"outcome": outcome, "trace": [f"complete_{outcome}"]}

        return node

    builder = StateGraph(EventAggregationState)
    builder.add_node("load_message", load_message)
    builder.add_node("minimal_filter", minimal_filter_node)
    builder.add_node("candidate_retrieval", candidates_node)
    builder.add_node("semantic_decision", decision_node)
    builder.add_node("checkpoint_decision", checkpoint_decision)
    builder.add_node("apply_membership", membership_node)
    builder.add_node("refresh_projection", projection_node)
    builder.add_node("complete_skipped", terminal("skipped_by_minimal_filter"))
    builder.add_node("complete_preview", terminal("preview_completed"))
    builder.add_edge(START, "load_message")
    builder.add_edge("load_message", "minimal_filter")
    builder.add_conditional_edges(
        "minimal_filter",
        route_admission,
        {"process": "candidate_retrieval", "skip": "complete_skipped"},
    )
    builder.add_edge("candidate_retrieval", "semantic_decision")
    builder.add_edge("semantic_decision", "checkpoint_decision")
    builder.add_conditional_edges(
        "checkpoint_decision",
        route_after_decision,
        {
            "apply": "apply_membership",
            "preview": "complete_preview",
        },
    )
    builder.add_edge("apply_membership", "refresh_projection")
    builder.add_edge("refresh_projection", END)
    builder.add_edge("complete_skipped", END)
    builder.add_edge("complete_preview", END)
    return builder.compile(checkpointer=checkpointer)
