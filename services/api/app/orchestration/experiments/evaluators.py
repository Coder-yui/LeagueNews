"""Task-aware evaluators for frozen, offline experiment results.

The evaluators deliberately keep gold metrics separate from fixture diagnostics.
Synthetic and model-prefilled labels are useful for checking the execution path,
but they are not treated as human truth.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from app.orchestration.experiments.contracts import (
    CaseResult,
    ExperimentCase,
    ExperimentDataset,
    ExperimentTarget,
    LabelSource,
)


class FrozenTaskEvaluator:
    """Evaluate one target while preserving the provenance of every label."""

    def __init__(self, target: ExperimentTarget) -> None:
        self.target = target

    def evaluate(
        self,
        *,
        dataset: ExperimentDataset,
        cases: list[CaseResult],
    ) -> dict[str, object]:
        by_id = {case.case_id: case for case in dataset.cases}
        labels = Counter(by_id[case.case_id].label_source for case in cases)
        human_cases = [
            (by_id[result.case_id], result)
            for result in cases
            if by_id[result.case_id].label_source == LabelSource.HUMAN_CONFIRMED
        ]
        base: dict[str, object] = {
            "target": self.target,
            "label_counts": {str(source): count for source, count in labels.items()},
            "gold_eligible": len(human_cases),
            "pending_human_review": len(cases) - len(human_cases),
            "failed": sum(result.status == "failed" for result in cases),
            "invalid": sum(result.status == "invalid" for result in cases),
            "duration_ms": _sum_number(result.duration_ms for result in cases),
            "call_count": sum(result.call_count for result in cases),
            "token_count": _sum_optional_int(result.token_count for result in cases),
            "cost_usd": _sum_number(result.cost_usd for result in cases),
            "manual_review_required": sum(
                result.manual_review_required for result in cases
            ),
        }
        gold = self._evaluate_gold(human_cases)
        base["gold_metrics"] = gold
        base["diagnostic_metrics"] = self._evaluate_diagnostics(dataset, cases)
        base["by_split"] = _split_summary(dataset, cases)
        return base

    def _evaluate_gold(
        self, cases: list[tuple[ExperimentCase, CaseResult]]
    ) -> dict[str, object] | None:
        if not cases:
            return None
        if self.target in {
            ExperimentTarget.MESSAGE_ANALYSIS,
            ExperimentTarget.ITEM_PROCESSING,
        }:
            metrics = _field_metrics(cases, actual_key="message_analysis")
            metrics["summary"] = _summary_metrics(cases)
            return metrics
        if self.target in {
            ExperimentTarget.IMPORTANCE_SCORING,
            ExperimentTarget.MESSAGE_IMPORTANCE,
        }:
            return _importance_metrics(cases)
        if self.target in {
            ExperimentTarget.EVENT_AGGREGATION,
            ExperimentTarget.EVENT_IMPORTANCE,
        }:
            return _event_metrics(cases)
        if self.target == ExperimentTarget.FEATURED_SELECTION:
            return _selection_metrics(cases, actual_key="featured_plan")
        if self.target == ExperimentTarget.DAILY_REPORT:
            return _daily_report_metrics(cases)
        if self.target == ExperimentTarget.END_TO_END:
            return _end_to_end_metrics(cases)
        return None

    def _evaluate_diagnostics(
        self, dataset: ExperimentDataset, cases: list[CaseResult]
    ) -> dict[str, object]:
        """Expose fixture checks without naming them accuracy."""

        expected_by_id = {case.case_id: case.label_values for case in dataset.cases}
        comparable = 0
        exact = 0
        for result in cases:
            expected = expected_by_id.get(result.case_id) or {}
            if result.status != "succeeded" or not expected or result.actual is None:
                continue
            comparable += 1
            if _diagnostic_projection(
                result.actual, expected, self.target
            ) == _diagnostic_projection(expected, expected, self.target):
                exact += 1
        return {
            "name": "fixture_or_label_projection_check",
            "is_gold": False,
            "comparable_cases": comparable,
            "exact_projection_matches": exact,
            "note": "Diagnostic only; synthetic/model-prefill labels are not quality claims.",
        }


def default_evaluators() -> dict[ExperimentTarget, FrozenTaskEvaluator]:
    evaluators: dict[ExperimentTarget, FrozenTaskEvaluator] = {}
    for target in ExperimentTarget:
        evaluators[target] = FrozenTaskEvaluator(target)
    return evaluators


def _field_metrics(
    cases: list[tuple[ExperimentCase, CaseResult]], *, actual_key: str
) -> dict[str, object]:
    totals: Counter[str] = Counter()
    matches: Counter[str] = Counter()
    for case, result in cases:
        expected = case.label_values
        actual = (result.actual or {}).get(actual_key, {}) if result.actual else {}
        if result.status != "succeeded" or not isinstance(actual, dict):
            for field in expected:
                totals[field] += 1
            continue
        for field, expected_value in expected.items():
            totals[field] += 1
            if actual.get(field) == expected_value:
                matches[field] += 1
    return {
        "field_accuracy": {
            field: matches[field] / totals[field]
            for field in sorted(totals)
        },
        "fields": {field: totals[field] for field in sorted(totals)},
    }


def _importance_metrics(
    cases: list[tuple[ExperimentCase, CaseResult]],
) -> dict[str, object]:
    totals: Counter[str] = Counter()
    matches: Counter[str] = Counter()
    expected_scores: list[float] = []
    actual_scores: list[float] = []
    for case, result in cases:
        expected = case.label_values
        actual = (result.actual or {}).get("importance", {}) if result.actual else {}
        for field, expected_value in expected.items():
            if field == "importance_score":
                continue
            totals[field] += 1
            if isinstance(actual, dict) and actual.get(field) == expected_value:
                matches[field] += 1
        expected_score = case.label_values.get("score")
        if expected_score is None:
            expected_score = case.label_values.get("importance_score")
        actual_score = (result.actual or {}).get("importance_score") if result.actual else None
        if isinstance(expected_score, (int, float)) and isinstance(
            actual_score, (int, float)
        ):
            expected_scores.append(float(expected_score))
            actual_scores.append(float(actual_score))
    return {
        "field_accuracy": {
            field: matches[field] / totals[field]
            for field in sorted(totals)
        },
        "fields": {field: totals[field] for field in sorted(totals)},
        "score_mae": (
            round(
                sum(
                    abs(expected - actual)
                    for expected, actual in zip(expected_scores, actual_scores)
                )
                / len(expected_scores),
                10,
            )
            if expected_scores
            else None
        ),
        "score_cases": len(expected_scores),
        "relative_order_accuracy": _pairwise_order_accuracy(expected_scores, actual_scores),
    }


def _summary_metrics(
    cases: list[tuple[ExperimentCase, CaseResult]],
) -> dict[str, object]:
    """Score fact coverage and unsupported content when those labels exist."""

    coverage: list[float] = []
    unsupported: list[float] = []
    for case, result in cases:
        expected_facts = case.label_values.get("summary_facts")
        actual_summary = (result.actual or {}).get("message_analysis", {}).get("summary", "")
        if not isinstance(expected_facts, list) or not isinstance(actual_summary, str):
            continue
        normalized = actual_summary.casefold()
        coverage.append(
            sum(str(fact).casefold() in normalized for fact in expected_facts)
            / len(expected_facts)
            if expected_facts
            else 1.0
        )
        unsupported_facts = case.label_values.get("unsupported_summary_facts", [])
        if isinstance(unsupported_facts, list):
            unsupported.append(
                sum(str(fact).casefold() in normalized for fact in unsupported_facts)
            )
    return {
        "fact_coverage": sum(coverage) / len(coverage) if coverage else None,
        "unsupported_fact_mentions": sum(unsupported) if unsupported else None,
        "cases_with_fact_labels": len(coverage),
    }


def _event_metrics(
    cases: list[tuple[ExperimentCase, CaseResult]],
) -> dict[str, object]:
    expected_memberships: list[tuple[str, str, str]] = []
    actual_memberships: list[tuple[str, str, str]] = []
    update_matches = 0
    update_total = 0
    false_merge = 0
    false_split = 0
    for case, result in cases:
        expected = case.label_values.get("mentions", [])
        actual = (result.actual or {}).get("event_decision", {}) if result.actual else {}
        predicted = actual.get("mentions", []) if isinstance(actual, dict) else []
        expected_keys = _event_keys(expected)
        actual_keys = _event_keys(predicted)
        expected_memberships.extend(expected_keys)
        actual_memberships.extend(actual_keys)
        expected_event_groups = _event_groups(expected)
        actual_event_groups = _event_groups(predicted)
        false_merge += sum(
            1
            for group in actual_event_groups
            if len(group) > 1 and not _same_expected_group(group, expected_event_groups)
        )
        false_split += sum(
            1
            for group in expected_event_groups
            if len(group) > 1 and not _same_actual_group(group, actual_event_groups)
        )
        for expected_row in expected:
            if "update" in expected_row:
                update_total += 1
                actual_rows = actual.get("mentions", []) if isinstance(actual, dict) else []
                if expected_row["update"] == _find_update(actual_rows, expected_row):
                    update_matches += 1
    expected_set = Counter(expected_memberships)
    actual_set = Counter(actual_memberships)
    true_positive = sum((expected_set & actual_set).values())
    predicted = sum(actual_set.values())
    labeled = sum(expected_set.values())
    return {
        "membership_precision": true_positive / predicted if predicted else None,
        "membership_recall": true_positive / labeled if labeled else None,
        "false_merge_count": false_merge,
        "false_split_count": false_split,
        "update_correctness": update_matches / update_total if update_total else None,
        "update_cases": update_total,
    }


def _selection_metrics(
    cases: list[tuple[ExperimentCase, CaseResult]], *, actual_key: str
) -> dict[str, object]:
    coverage: list[float] = []
    rank_scores: list[float] = []
    duplicate_cases = 0
    capacity_violations = 0
    for case, result in cases:
        expected = case.label_values
        actual_plan = (result.actual or {}).get(actual_key, {}) if result.actual else {}
        actual_ids = list(actual_plan.get("selected_item_ids", [])) if isinstance(actual_plan, dict) else []
        expected_ids = list(expected.get("selected_item_ids", []))
        if expected_ids:
            coverage.append(len(set(actual_ids) & set(expected_ids)) / len(set(expected_ids)))
        if len(actual_ids) != len(set(actual_ids)):
            duplicate_cases += 1
        limit = expected.get("capacity")
        if isinstance(limit, int) and len(actual_ids) > limit:
            capacity_violations += 1
        rank_scores.append(_ordered_overlap(expected_ids, actual_ids))
    return {
        "required_coverage": sum(coverage) / len(coverage) if coverage else None,
        "ranked_overlap": sum(rank_scores) / len(rank_scores) if rank_scores else None,
        "duplicate_cases": duplicate_cases,
        "capacity_violations": capacity_violations,
    }


def _daily_report_metrics(
    cases: list[tuple[ExperimentCase, CaseResult]],
) -> dict[str, object]:
    required = []
    overlaps = []
    duplicates = 0
    capacity_violations = 0
    section_violations = 0
    for case, result in cases:
        expected = case.label_values
        actual = (result.actual or {}).get("daily_plan", {}) if result.actual else {}
        actual_sections = actual.get("sections", {}) if isinstance(actual, dict) else {}
        expected_sections = expected.get("sections", {})
        expected_ids = [item.get("message_id") for rows in expected_sections.values() for item in rows]
        actual_ids = [item.get("message_id") for rows in actual_sections.values() for item in rows]
        required_ids = [item for item in expected.get("required_message_ids", [])]
        if required_ids:
            required.append(len(set(required_ids) & set(actual_ids)) / len(set(required_ids)))
        overlaps.append(_ordered_overlap(expected_ids, actual_ids))
        if len(actual_ids) != len(set(actual_ids)):
            duplicates += 1
        limits = expected.get("section_limits", {})
        for section, rows in actual_sections.items():
            if isinstance(limits.get(section), int) and len(rows) > limits[section]:
                capacity_violations += 1
        if set(actual_sections) - {"lolpc", "esports", "tft", "other"}:
            section_violations += 1
    return {
        "required_message_coverage": sum(required) / len(required) if required else None,
        "ranked_overlap": sum(overlaps) / len(overlaps) if overlaps else None,
        "duplicate_cases": duplicates,
        "section_capacity_violations": capacity_violations,
        "section_violations": section_violations,
    }


def _end_to_end_metrics(
    cases: list[tuple[ExperimentCase, CaseResult]],
) -> dict[str, object]:
    complete = 0
    distribution_cases = 0
    for _case, result in cases:
        actual = result.actual or {}
        if result.status != "succeeded":
            continue
        message_results = actual.get("message_results")
        if isinstance(message_results, list) and message_results:
            complete += 1
        if isinstance(actual.get("featured_plan"), dict) and isinstance(
            actual.get("daily_plan"), dict
        ):
            distribution_cases += 1
    return {
        "complete_message_to_distribution_cases": complete,
        "cases_with_featured_and_daily_plans": distribution_cases,
        "note": "Pipeline integrity diagnostic; not a domain-quality metric.",
    }


def _event_keys(rows: Any) -> list[tuple[str, str, str]]:
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows:
        if not isinstance(row, dict) or row.get("action") == "ignore":
            continue
        result.append(
            (
                str(row.get("action") or ""),
                str(row.get("event_id") or row.get("event_key") or ""),
                str(row.get("event_family") or ""),
            )
        )
    return result


def _event_groups(rows: Any) -> list[list[str]]:
    groups: dict[str, list[str]] = {}
    if not isinstance(rows, list):
        return []
    for row in rows:
        if not isinstance(row, dict) or row.get("action") == "ignore":
            continue
        new_event = row.get("new_event")
        new_event_title = new_event.get("title") if isinstance(new_event, dict) else None
        group = str(row.get("event_id") or row.get("event_key") or new_event_title or "")
        groups.setdefault(group, []).append(str(row.get("mention_index")))
    return list(groups.values())


def _same_expected_group(group: list[str], groups: list[list[str]]) -> bool:
    return any(set(group) <= set(other) for other in groups)


def _same_actual_group(group: list[str], groups: list[list[str]]) -> bool:
    return any(set(group) <= set(other) for other in groups)


def _find_update(rows: Any, expected: dict[str, Any]) -> Any:
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("mention_index") == expected.get("mention_index"):
            return row.get("projection") or row.get("materiality")
    return None


def _ordered_overlap(expected: list[Any], actual: list[Any]) -> float:
    if not expected:
        return 1.0 if not actual else 0.0
    positions = {value: index for index, value in enumerate(actual)}
    present = [positions[value] for value in expected if value in positions]
    if not present:
        return 0.0
    inversions = sum(left > right for index, left in enumerate(present) for right in present[index + 1 :])
    return (len(present) / len(expected)) * (1 - inversions / max(len(present) * (len(present) - 1) / 2, 1))


def _pairwise_order_accuracy(expected: list[float], actual: list[float]) -> float | None:
    if len(expected) < 2:
        return None
    total = correct = 0
    for index, expected_left in enumerate(expected):
        for right_index, expected_right in enumerate(expected[index + 1 :], start=index + 1):
            total += 1
            expected_direction = expected_left > expected_right
            actual_direction = actual[index] > actual[right_index]
            correct += expected_direction == actual_direction
    return correct / total if total else None


def _diagnostic_projection(
    value: dict[str, Any], expected: dict[str, Any], target: ExperimentTarget
) -> Any:
    if value is expected:
        return expected
    if target in {
        ExperimentTarget.MESSAGE_ANALYSIS,
        ExperimentTarget.ITEM_PROCESSING,
    }:
        candidate = value.get("message_analysis", {})
    elif target in {
        ExperimentTarget.IMPORTANCE_SCORING,
        ExperimentTarget.MESSAGE_IMPORTANCE,
    }:
        candidate = {
            **(value.get("importance", {}) if isinstance(value.get("importance"), dict) else {}),
            "importance_score": value.get("importance_score"),
        }
    elif target == ExperimentTarget.FEATURED_SELECTION:
        candidate = value.get("featured_plan", {})
    elif target == ExperimentTarget.DAILY_REPORT:
        candidate = value.get("daily_plan", {})
    else:
        candidate = value
    return {key: candidate.get(key) for key in expected if isinstance(candidate, dict)}


def _sum_number(values: Iterable[float | None]) -> float:
    return sum(float(value) for value in values if value is not None)


def _sum_optional_int(values: Iterable[int | None]) -> int:
    return sum(int(value) for value in values if value is not None)


def _split_summary(
    dataset: ExperimentDataset, cases: list[CaseResult]
) -> dict[str, dict[str, int]]:
    source_by_id = {case.case_id: case for case in dataset.cases}
    result: dict[str, dict[str, int]] = {}
    for case in cases:
        split = source_by_id[case.case_id].split
        bucket = result.setdefault(split, {"succeeded": 0, "failed": 0, "invalid": 0})
        bucket[case.status] += 1
    return result
