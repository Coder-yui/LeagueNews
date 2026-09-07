"""Provider calls and collection policies; no jobs, review or publication writes."""

from typing import Any
from collections.abc import Sequence
from app.methods.contracts import MethodSelection, FeaturedCandidate, FeaturedPlan, DailyReportPlan
from app.domain.importance import FEATURED_MESSAGE_MIN_IMPORTANCE
from app.domain.daily_report import (
    DailyReportCandidate,
    assign_daily_sections,
    deduplicate_daily_candidates,
    eligible_daily_candidates,
    rank_daily_sections,
    DAILY_REPORT_MIN_IMPORTANCE,
    DAILY_REPORT_SECTION_LIMITS,
)


def _configured_client(client: Any, selection: MethodSelection) -> Any:
    if selection.prompt_ref or selection.model_parameters:
        return client.configured(
            prompt_ref=selection.prompt_ref,
            prompt_contents=selection.prompt_contents,
            model_parameters=selection.model_parameters,
        )
    return client


async def _message_analysis_baseline(
    *, client: Any, payload: dict[str, Any], selection: MethodSelection
) -> Any:
    client = _configured_client(client, selection)
    return await client.analyze_message_content(**payload)


async def _importance_scoring_baseline(
    *, client: Any, payload: dict[str, Any], selection: MethodSelection
) -> Any:
    client = _configured_client(client, selection)
    return await client.classify_and_score_importance(**payload)


async def _event_aggregation_baseline(
    *, client: Any, payload: dict[str, Any], selection: MethodSelection
) -> Any:
    client = _configured_client(client, selection)
    return await client.aggregate_events(**payload)


def _featured_policy(
    *, candidates: Sequence[FeaturedCandidate], selection: MethodSelection
) -> FeaturedPlan:
    if selection.implementation == "top_n":
        strategy = selection.strategy_parameters
        max_items = max(int(strategy.get("max_items", 3)), 0)
        min_score = float(strategy.get("min_importance_score", 0))
        ranked = sorted(
            (
                candidate
                for candidate in candidates
                if candidate.content_form != "repost" and candidate.importance_score >= min_score
            ),
            key=lambda candidate: (
                -candidate.importance_score,
                candidate.normalized_item_id,
            ),
        )[:max_items]
        selected = tuple(candidate.normalized_item_id for candidate in ranked)
        return FeaturedPlan(
            selected_item_ids=selected,
            decisions=tuple(
                {
                    "normalized_item_id": candidate.normalized_item_id,
                    "selected": candidate.normalized_item_id in selected,
                    "reason": "top_n_ranked",
                }
                for candidate in candidates
            ),
        )
    if selection.implementation != "threshold":
        raise ValueError("unknown featured selection implementation")
    strategy = selection.strategy_parameters
    threshold = strategy.get("min_importance_score")
    selected = tuple(
        candidate.normalized_item_id
        for candidate in candidates
        if (
            candidate.content_form != "repost"
            and candidate.importance_score
            >= float(threshold if threshold is not None else FEATURED_MESSAGE_MIN_IMPORTANCE)
        )
    )
    plan = FeaturedPlan(
        selected_item_ids=selected,
        decisions=tuple(
            {
                "normalized_item_id": candidate.normalized_item_id,
                "selected": candidate.normalized_item_id in selected,
                "reason": "threshold_baseline",
            }
            for candidate in candidates
        ),
    )
    return plan


def _daily_policy(
    *, candidates: Sequence[DailyReportCandidate], selection: MethodSelection
) -> DailyReportPlan:
    if selection.implementation == "balanced":
        strategy = selection.strategy_parameters
        values = _balanced_daily_sections(
            list(candidates),
            section_limits={
                str(section): int(limit)
                for section, limit in (strategy.get("section_limits") or {}).items()
                if str(section) in DAILY_REPORT_SECTION_LIMITS
                and isinstance(limit, int)
                and not isinstance(limit, bool)
                and limit >= 0
            },
        )
        return DailyReportPlan(
            sections={
                section: tuple(
                    {
                        "message_id": candidate.message_id,
                        "importance_score": candidate.importance_score,
                        "published_at": candidate.published_at.isoformat(),
                        "content_form": candidate.content_form,
                        "products": list(candidate.products),
                        "event_ids": list(candidate.event_ids),
                    }
                    for candidate in section_items
                )
                for section, section_items in values.items()
            }
        )
    if selection.implementation != "baseline":
        raise ValueError("unknown daily report implementation")
    strategy = selection.strategy_parameters
    raw_limits = strategy.get("section_limits") or {}
    section_limits = {
        str(section): int(limit)
        for section, limit in raw_limits.items()
        if str(section) in {"lolpc", "esports", "tft", "other"}
        and isinstance(limit, int)
        and not isinstance(limit, bool)
        and limit >= 0
    }
    values = rank_daily_sections(
        assign_daily_sections(
            deduplicate_daily_candidates(eligible_daily_candidates(list(candidates)))
        ),
        section_limits=section_limits,
    )
    plan = DailyReportPlan(
        sections={
            section: tuple(
                {
                    "message_id": candidate.message_id,
                    "importance_score": candidate.importance_score,
                    "published_at": candidate.published_at.isoformat(),
                    "content_form": candidate.content_form,
                    "products": list(candidate.products),
                    "event_ids": list(candidate.event_ids),
                }
                for candidate in values
            )
            for section, values in values.items()
        }
    )
    return plan


def _balanced_daily_sections(
    candidates: list[DailyReportCandidate], *, section_limits: dict[str, int]
) -> dict[str, list[DailyReportCandidate]]:
    eligible = [
        candidate
        for candidate in candidates
        if candidate.content_form == "original"
        and candidate.importance_score >= DAILY_REPORT_MIN_IMPORTANCE
    ]
    eligible.sort(key=lambda candidate: candidate.published_at, reverse=True)
    seen_events: set[int] = set()
    deduplicated: list[DailyReportCandidate] = []
    for candidate in eligible:
        event_ids = set(candidate.event_ids)
        if event_ids & seen_events:
            continue
        seen_events.update(event_ids)
        deduplicated.append(candidate)
    sections = assign_daily_sections(deduplicated)
    limits = {**DAILY_REPORT_SECTION_LIMITS, **section_limits}
    return {
        section: sorted(
            section_items,
            key=lambda candidate: (
                candidate.published_at,
                candidate.importance_score,
                candidate.message_id,
            ),
            reverse=True,
        )[: limits[section]]
        for section, section_items in sections.items()
    }


def _recall_policy(*, payload, selection):
    from app.domain.event_recall import rank_event_candidates

    values = {**payload}
    values["total_limit"] = payload.get("total_limit") or selection.strategy_parameters.get(
        "total_limit", 12
    )
    values["window_days"] = selection.strategy_parameters.get("window_days", 60)
    return rank_event_candidates(**values)


def _importance_calculation_policy(*, payload, selection):
    from app.domain.message_scoring import calculate_message_importance

    return calculate_message_importance(**payload)
