"""Select and invoke methods independently of workflow execution infrastructure."""

from collections.abc import Callable, Awaitable, Mapping, Sequence
from typing import Any
from app.domain.daily_report import DailyReportCandidate, DAILY_REPORT_SECTION_LIMITS
from app.methods.contracts import (
    MethodAssemblyConfig,
    MethodSelection,
    MethodCallRecord,
    MessageAnalysisInput,
    ImportanceScoringInput,
    EventAggregationInput,
    FeaturedCandidate,
    FeaturedPlan,
    DailyReportPlan,
)
from app.methods.baseline import (
    _message_analysis_baseline,
    _importance_scoring_baseline,
    _importance_calculation_policy,
    _event_aggregation_baseline,
    _featured_policy,
    _daily_policy,
    _recall_policy,
)
from app.methods.examples import (
    _message_analysis_heuristic,
    _importance_scoring_rule_v2,
    _event_aggregation_token_recall_v2,
)

MethodImplementation = Callable[..., Any | Awaitable[Any]]


class MethodAssembly:
    """Resolve and execute the small set of replaceable domain methods."""

    def __init__(
        self,
        config: MethodAssemblyConfig | None = None,
        *,
        implementations: Mapping[str, Mapping[str, MethodImplementation]] | None = None,
    ) -> None:
        if config is None:
            from app.core.config import settings

            config = MethodAssemblyConfig.model_validate(settings.processing_method_config)
        self.config = config
        self._implementations: dict[str, dict[str, MethodImplementation]] = {
            "message_analysis": {
                "baseline": _message_analysis_baseline,
                "heuristic": _message_analysis_heuristic,
            },
            "importance_calculation": {"baseline": _importance_calculation_policy},
            "importance_scoring": {
                "baseline": _importance_scoring_baseline,
                "rule_v2": _importance_scoring_rule_v2,
            },
            "event_recall": {"baseline": _recall_policy},
            "event_aggregation": {
                "baseline": _event_aggregation_baseline,
                "token_recall_v2": _event_aggregation_token_recall_v2,
            },
            "featured_selection": {"threshold": _featured_policy, "top_n": _featured_policy},
            "daily_report": {"baseline": _daily_policy, "balanced": _daily_policy},
        }
        for method, values in (implementations or {}).items():
            self._implementations.setdefault(method, {}).update(values)
        self._calls: list[MethodCallRecord] = []

    def model_identifier(self, method: str) -> str:
        selection = self.select(method)
        if selection.implementation != "baseline":
            return f"method:{selection.implementation}"
        from app.core.config import settings

        return str(selection.model_parameters.get("model", settings.model_name))

    def select(self, method: str) -> MethodSelection:
        implementation = self.config.implementation_for(method)
        if implementation not in self._implementations.get(method, {}):
            raise ValueError(f"method implementation is not registered: {method}:{implementation}")
        return MethodSelection(
            method=method,
            implementation=implementation,
            prompt_ref=self.config.prompt_refs.get(method),
            model_parameters=dict(self.config.model_parameters.get(method) or {}),
            strategy_parameters=dict(self.config.strategy_parameters.get(method) or {}),
            prompt_contents=dict(self.config.prompt_contents),
        )

    async def invoke(
        self,
        method: str,
        *,
        client: Any,
        payload: dict[str, Any],
    ) -> Any:
        selection = self.select(method)
        implementation = self._implementations[method][selection.implementation]
        self._calls.append(
            MethodCallRecord(
                method=selection.method,
                implementation=selection.implementation,
                prompt_ref=selection.prompt_ref,
                model_parameters=selection.model_parameters,
                strategy_parameters=selection.strategy_parameters,
            )
        )
        return await implementation(
            client=client,
            payload=payload,
            selection=selection,
        )

    async def analyze_message(self, value: MessageAnalysisInput, *, client: Any) -> Any:
        return await self.invoke(
            "message_analysis",
            client=client,
            payload=value.model_dump(mode="json"),
        )

    async def score_importance(self, value: ImportanceScoringInput, *, client: Any) -> Any:
        return await self.invoke(
            "importance_scoring",
            client=client,
            payload=value.model_dump(mode="json"),
        )

    async def aggregate_events(self, value: EventAggregationInput, *, client: Any) -> Any:
        return await self.invoke(
            "event_aggregation",
            client=client,
            payload=value.model_dump(mode="json"),
        )

    @property
    def calls(self) -> tuple[MethodCallRecord, ...]:
        return tuple(self._calls)

    def last_call(self, method: str) -> MethodCallRecord:
        for call in reversed(self._calls):
            if call.method == method:
                return call
        raise LookupError(f"method has not been called: {method}")

    def record_call(self, method: str) -> MethodSelection:
        """Record a synchronous baseline method selection."""

        selection = self.select(method)
        self._calls.append(
            MethodCallRecord(
                method=selection.method,
                implementation=selection.implementation,
                prompt_ref=selection.prompt_ref,
                model_parameters=selection.model_parameters,
                strategy_parameters=selection.strategy_parameters,
            )
        )
        return selection

    def calculate_importance(self, **payload):
        selection = self.record_call("importance_calculation")
        return self._implementations["importance_calculation"][selection.implementation](
            payload=payload, selection=selection
        )

    def recall_events(self, **payload):
        selection = self.record_call("event_recall")
        return self._implementations["event_recall"][selection.implementation](
            payload=payload, selection=selection
        )

    def select_featured(self, candidates: Sequence[FeaturedCandidate]) -> FeaturedPlan:
        selection = self.record_call("featured_selection")
        implementation = self._implementations["featured_selection"][selection.implementation]
        plan = FeaturedPlan.model_validate(
            implementation(candidates=candidates, selection=selection)
        )
        allowed = {candidate.normalized_item_id for candidate in candidates}
        if not set(plan.selected_item_ids).issubset(allowed) or len(
            set(plan.selected_item_ids)
        ) != len(plan.selected_item_ids):
            raise ValueError("featured plan must select unique input candidates")
        return plan

    def plan_daily_report(self, candidates: Sequence[DailyReportCandidate]) -> DailyReportPlan:
        selection = self.record_call("daily_report")
        implementation = self._implementations["daily_report"][selection.implementation]
        plan = DailyReportPlan.model_validate(
            implementation(candidates=candidates, selection=selection)
        )
        ids = [row["message_id"] for rows in plan.sections.values() for row in rows]
        if (
            not set(plan.sections).issubset(DAILY_REPORT_SECTION_LIMITS)
            or len(ids) != len(set(ids))
            or not set(ids).issubset({row.message_id for row in candidates})
        ):
            raise ValueError("daily plan must assign unique input candidates to valid sections")
        return plan
