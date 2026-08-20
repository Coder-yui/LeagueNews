import operator
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field, model_validator

from app.orchestration.contracts import ReviewDecision, ReviewMode, RunMode


DAILY_REPORT_GRAPH = "daily_report_generation"
DAILY_REPORT_GRAPH_VERSION = "v3.0.0-dev2"
DAILY_REPORT_STATE_VERSION = 1


class DailyReportStage(StrEnum):
    LOAD_WINDOW = "load_window"
    SELECT_CANDIDATES = "select_candidates"
    DEDUPLICATE_EVENTS = "deduplicate_events"
    ASSIGN_SECTIONS = "assign_sections"
    RANK_ITEMS = "rank_items"
    PUBLISH = "publish"


DAILY_REPORT_STAGE_ORDER = tuple(DailyReportStage)


class DailyReportRequest(BaseModel):
    workflow_run_id: int = Field(ge=1)
    report_date: date
    run_mode: RunMode
    review_mode: ReviewMode = ReviewMode.AUTOMATIC
    batch_id: int | None = Field(default=None, ge=1)
    graph_version: str = DAILY_REPORT_GRAPH_VERSION
    state_version: int = DAILY_REPORT_STATE_VERSION

    @model_validator(mode="after")
    def validate_scope(self) -> "DailyReportRequest":
        if self.run_mode == RunMode.EXPERIMENT and self.batch_id is None:
            raise ValueError("experiment runs require batch_id")
        if self.run_mode != RunMode.EXPERIMENT and self.batch_id is not None:
            raise ValueError("batch_id is only valid for experiment runs")
        return self

    @property
    def thread_id(self) -> str:
        scope = f"batch:{self.batch_id}" if self.batch_id is not None else "live"
        return (
            f"{DAILY_REPORT_GRAPH}:{self.graph_version}:{self.run_mode}:"
            f"{scope}:run:{self.workflow_run_id}:date:{self.report_date.isoformat()}"
        )


class DailyWindow(BaseModel):
    report_date: date
    window_start: datetime
    window_end: datetime


class DailyCandidate(BaseModel):
    message_id: int = Field(ge=1)
    importance_score: float = Field(ge=0, le=1)
    published_at: datetime
    content_form: str
    products: list[str] = Field(default_factory=list)
    event_ids: list[int] = Field(default_factory=list)


class DailyCandidateSet(BaseModel):
    candidates: list[DailyCandidate] = Field(default_factory=list)


class DailySections(BaseModel):
    sections: dict[str, list[DailyCandidate]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_sections(self) -> "DailySections":
        allowed = {"lolpc", "esports", "tft", "other"}
        if not set(self.sections).issubset(allowed):
            raise ValueError("daily report contains an unknown section")
        message_ids = [
            candidate.message_id
            for candidates in self.sections.values()
            for candidate in candidates
        ]
        if len(message_ids) != len(set(message_ids)):
            raise ValueError("a daily report message can appear in only one section")
        return self


class DailyPublication(BaseModel):
    daily_report_id: int = Field(ge=1)
    item_count: int = Field(ge=0)


class DailyReportState(TypedDict, total=False):
    request: dict[str, object]
    window: dict[str, object]
    selected: dict[str, object]
    deduplicated: dict[str, object]
    assigned: dict[str, object]
    ranked: dict[str, object]
    review_decision: dict[str, object]
    publication: dict[str, object]
    outcome: str
    trace: Annotated[list[str], operator.add]


class DailyReportBackend(Protocol):
    async def load_window(self, request: DailyReportRequest) -> DailyWindow: ...

    async def select_candidates(
        self, request: DailyReportRequest, window: DailyWindow
    ) -> DailyCandidateSet: ...

    async def deduplicate_events(
        self, candidates: DailyCandidateSet
    ) -> DailyCandidateSet: ...

    async def assign_sections(self, candidates: DailyCandidateSet) -> DailySections: ...

    async def rank_items(self, sections: DailySections) -> DailySections: ...

    async def publish(
        self, request: DailyReportRequest, sections: DailySections
    ) -> DailyPublication: ...


def build_daily_report_graph(
    backend: DailyReportBackend,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
):
    async def load_window(state: DailyReportState) -> DailyReportState:
        value = await backend.load_window(
            DailyReportRequest.model_validate(state["request"])
        )
        return {"window": value.model_dump(mode="json"), "trace": ["load_window"]}

    async def select_candidates(state: DailyReportState) -> DailyReportState:
        value = await backend.select_candidates(
            DailyReportRequest.model_validate(state["request"]),
            DailyWindow.model_validate(state["window"]),
        )
        return {"selected": value.model_dump(mode="json"), "trace": ["select_candidates"]}

    async def deduplicate(state: DailyReportState) -> DailyReportState:
        value = await backend.deduplicate_events(
            DailyCandidateSet.model_validate(state["selected"])
        )
        return {"deduplicated": value.model_dump(mode="json"), "trace": ["deduplicate_events"]}

    async def assign(state: DailyReportState) -> DailyReportState:
        value = await backend.assign_sections(
            DailyCandidateSet.model_validate(state["deduplicated"])
        )
        return {"assigned": value.model_dump(mode="json"), "trace": ["assign_sections"]}

    async def rank(state: DailyReportState) -> DailyReportState:
        value = await backend.rank_items(DailySections.model_validate(state["assigned"]))
        return {"ranked": value.model_dump(mode="json"), "trace": ["rank_items"]}

    def review(state: DailyReportState) -> DailyReportState:
        request = DailyReportRequest.model_validate(state["request"])
        if request.review_mode == ReviewMode.MANUAL:
            decision = ReviewDecision.model_validate(
                interrupt(
                    {
                        "kind": "daily_report_review",
                        "request": state["request"],
                        "proposal": state["ranked"],
                    }
                )
            )
            source = "manual"
        else:
            decision = ReviewDecision(action="approve", note="automatic policy approval")
            source = "automatic"
        result: DailyReportState = {
            "review_decision": decision.model_dump(mode="json"),
            "trace": [f"review_{source}"],
        }
        if decision.replacement is not None:
            result["ranked"] = DailySections.model_validate(
                decision.replacement
            ).model_dump(mode="json")
        return result

    def route_review(state: DailyReportState) -> str:
        if ReviewDecision.model_validate(state["review_decision"]).action == "reject":
            return "reject"
        request = DailyReportRequest.model_validate(state["request"])
        return "publish" if request.run_mode == RunMode.PRODUCTION else "preview"

    async def publish(state: DailyReportState) -> DailyReportState:
        result = await backend.publish(
            DailyReportRequest.model_validate(state["request"]),
            DailySections.model_validate(state["ranked"]),
        )
        return {
            "publication": result.model_dump(mode="json"),
            "outcome": "published",
            "trace": ["publish"],
        }

    def terminal(outcome: str):
        def node(_state: DailyReportState) -> DailyReportState:
            return {"outcome": outcome, "trace": [f"complete_{outcome}"]}

        return node

    builder = StateGraph(DailyReportState)
    builder.add_node("load_window", load_window)
    builder.add_node("select_candidates", select_candidates)
    builder.add_node("deduplicate_events", deduplicate)
    builder.add_node("assign_sections", assign)
    builder.add_node("rank_items", rank)
    builder.add_node("review", review)
    builder.add_node("publish", publish)
    builder.add_node("complete_preview", terminal("preview_completed"))
    builder.add_node("complete_rejected", terminal("review_rejected"))
    builder.add_edge(START, "load_window")
    builder.add_edge("load_window", "select_candidates")
    builder.add_edge("select_candidates", "deduplicate_events")
    builder.add_edge("deduplicate_events", "assign_sections")
    builder.add_edge("assign_sections", "rank_items")
    builder.add_edge("rank_items", "review")
    builder.add_conditional_edges(
        "review",
        route_review,
        {
            "publish": "publish",
            "preview": "complete_preview",
            "reject": "complete_rejected",
        },
    )
    builder.add_edge("publish", END)
    builder.add_edge("complete_preview", END)
    builder.add_edge("complete_rejected", END)
    return builder.compile(checkpointer=checkpointer)
