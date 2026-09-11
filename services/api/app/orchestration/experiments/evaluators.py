"""Task-aware evaluators for frozen, offline experiment results.

The evaluators deliberately keep gold metrics separate from fixture diagnostics.
Synthetic and model-prefilled labels are useful for checking the execution path,
but they are not treated as human truth.
"""

from __future__ import annotations

from collections import Counter
from math import ceil
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
        supplied = {case.case_id: case for case in cases}
        cases = [supplied.get(case.case_id) or CaseResult(case_id=case.case_id, status="failed",
                  error_type="MissingResult") for case in dataset.cases]
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
        durations = sorted(result.duration_ms for result in cases if not result.cache_hit and result.duration_ms is not None)
        base["case_latency_ms"] = {"samples": len(durations), **{
            name: durations[max(ceil(len(durations) * quantile)-1, 0)] if durations else None
            for name, quantile in {"p50": 0.5, "p95": 0.95, "max": 1}.items()}}
        base["duration_note"] = "sum of case durations, not end-to-end wall time; cache excluded"
        base["sample_count"] = len(cases)
        base["failure_rate"] = sum(result.status != "succeeded" for result in cases) / len(cases) if cases else None
        base["cache_hits"] = sum(result.cache_hit for result in cases)
        base["known_cost_usd"] = sum(result.metadata.get("known_cost_usd", 0) for result in cases if not result.cache_hit)
        base["unknown_cost_cases"] = sum(result.cost_usd is None for result in cases)
        base["per_stage_calls"] = _stage_measurements(cases)
        gold = self._evaluate_gold(human_cases)
        base["gold_metrics"] = gold
        base["gold_unavailable_reason"] = "no human-confirmed labels" if not human_cases else None
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
        }:
            return _event_metrics(cases)
        if self.target == ExperimentTarget.EVENT_IMPORTANCE:
            return _importance_metrics(cases)
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
    errors: dict[str, Counter[str]] = {}
    sets: dict[str, Counter[str]] = {}
    for case, result in cases:
        expected = case.label_values.get(actual_key, case.label_values)
        actual = (result.actual or {}).get(actual_key, {})
        if result.status != "succeeded" or not isinstance(actual, dict):
            actual = {}
        for field, value in expected.items():
            if field in {"summary_facts", "unsupported_summary_facts", "semantic_review"}:
                continue
            totals[field] += 1
            prediction = actual.get(field)
            if field in {"products", "topics"} and isinstance(value, list):
                wanted, found = set(value), set(prediction) if isinstance(prediction, list) else set()
                bucket = sets.setdefault(field, Counter())
                bucket.update(tp=len(wanted & found), predicted=len(found), labeled=len(wanted))
                equal = field in actual and wanted == found
            else:
                equal = field in actual and prediction == value
            matches[field] += equal
            if not equal:
                errors.setdefault(field, Counter())[f"{value!r} -> {prediction!r}"] += 1
    return {
        "field_accuracy": {field: matches[field] / count for field, count in sorted(totals.items())},
        "fields": dict(sorted(totals.items())),
        "error_distribution": {field: dict(values) for field, values in errors.items()},
        "multilabel": {
            field: {
                "precision": values["tp"] / values["predicted"] if values["predicted"] else 0.0,
                "recall": values["tp"] / values["labeled"] if values["labeled"] else None,
                "f1": 2 * values["tp"] / (values["predicted"] + values["labeled"])
                if values["predicted"] + values["labeled"] else None,
                "exact_match": matches[field] / totals[field],
                "samples": totals[field],
            } for field, values in sets.items()
        },
    }


def _importance_metrics(
    cases: list[tuple[ExperimentCase, CaseResult]],
) -> dict[str, object]:
    dimensions = [
        (case.model_copy(update={"labels": case.labels.model_copy(update={"values": {
            key: value for key, value in case.label_values.items() if key not in {"score", "importance_score", "calculation"}
        }})}), result) for case, result in cases
    ]
    dimension_metrics = _field_metrics(dimensions, actual_key="importance")
    expected_scores: list[float] = []
    actual_scores: list[float] = []
    for case, result in cases:
        expected_score = case.label_values.get("score")
        if expected_score is None:
            expected_score = case.label_values.get("importance_score")
        actual_score = (result.actual or {}).get("importance_score") if result.actual else None
        if result.status == "succeeded" and isinstance(expected_score, (int, float)) and isinstance(
            actual_score, (int, float)
        ):
            expected_scores.append(float(expected_score))
            actual_scores.append(float(actual_score))
    return {
        **dimension_metrics,
        "formula_note": "Deterministic formula is tested separately from model dimensions and final score labels",
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
        "score_labeled_cases": sum(isinstance(case.label_values.get("score", case.label_values.get("importance_score")), (int, float)) for case, _ in cases),
        "score_missing_outputs": sum(isinstance(case.label_values.get("score", case.label_values.get("importance_score")), (int, float)) for case, _ in cases) - len(expected_scores),
        "score_note": "MAE is conditional on numeric outputs; missing outputs reported separately",
        "relative_order_accuracy": _pairwise_order_accuracy(expected_scores, actual_scores),
    }


def _summary_metrics(
    cases: list[tuple[ExperimentCase, CaseResult]],
) -> dict[str, object]:
    """Literal phrase diagnostics only; these do not establish semantic correctness."""

    coverage: list[float] = []
    unsupported: list[float] = []
    for case, result in cases:
        expected_facts = case.label_values.get("summary_facts")
        actual_summary = (result.actual or {}).get("message_analysis", {}).get("summary", "")
        if not isinstance(expected_facts, list) or not expected_facts or not isinstance(actual_summary, str):
            continue
        normalized = actual_summary.casefold() if result.status == "succeeded" else ""
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
        "literal_phrase_coverage": sum(coverage) / len(coverage) if coverage else None,
        "literal_forbidden_phrase_mentions": sum(unsupported) if unsupported else None,
        "cases_with_fact_labels": len(coverage),
    }


def _event_metrics(cases: list[tuple[ExperimentCase, CaseResult]]) -> dict[str, object]:
    """Compare mention relationships, scoped to each scenario, never database IDs.

    Labels use steps[step_id].mentions with stable mention_index/event_key.
    Existing-event recall is measured only after the gold event was visible.
    """
    expected_groups, actual_groups = {}, {}
    expected_nodes, actual_nodes = set(), set()
    recall_total = recall_hits = decision_errors = labeled_steps = missing_steps = 0
    details = []
    decision_totals, decision_matches = Counter(), Counter()
    for case, result in cases:
        labels = case.label_values
        label_steps = labels.get("steps")
        actual_steps = {str(row.get("step_id")): row for row in (result.actual or {}).get("steps", [])}
        if isinstance(label_steps, list):
            label_steps = {str(row["step_id"]): row for row in label_steps}
        if not isinstance(label_steps, dict):
            label_steps = {"single": labels}
            actual_steps = {"single": {"status": result.status, "actual": result.actual}}
        seen = set()
        predicted_ids_by_gold = {}
        order = [step.step_id for step in case.steps] or list(actual_steps)
        order += [step_id for step_id in label_steps if step_id not in order]
        for step_id in order:
            expected = label_steps.get(step_id, {})
            if not isinstance(expected, dict) or "mentions" not in expected:
                continue
            labeled_steps += 1
            step = actual_steps.get(str(step_id), {})
            output = step.get("actual") or {}
            success = step.get("status") == "succeeded"
            missing_steps += not success
            predicted = output.get("event_decision", {}).get("mentions", []) if success else []
            bindings = {row["mention_index"]: str(row["event_id"]) for row in output.get("event_memberships", [])}
            recalled = {str(row["event_id"]) for row in output.get("recalled_candidates", [])}
            expected_rows = expected["mentions"]
            valid_predictions = {row.get("mention_index", i): row for i, row in enumerate(predicted)
                                 if row.get("action") != "ignore"}
            new_seen = set()
            for i, row in enumerate(expected_rows):
                if row.get("action") == "ignore":
                    continue
                index = row.get("mention_index", i)
                key = row.get("event_key")
                if key is None:
                    details.append({"case_id": case.case_id, "step_id": step_id,
                                    "error": "label_missing_logical_event_key"})
                    continue
                node = (case.case_id, str(step_id), index)
                expected_nodes.add(node)
                expected_groups[node] = (case.case_id, str(key))
                prediction = valid_predictions.get(index)
                error = None
                for field in ("action", "event_family", "product", "materiality", "projection"):
                    if field in row:
                        decision_totals[field] += 1
                        decision_matches[field] += bool(prediction is not None and prediction.get(field) == row[field])
                if str(key) in seen:
                    recall_total += 1
                    hit = bool(predicted_ids_by_gold.get(str(key), set()) & recalled)
                    recall_hits += hit
                    if not hit:
                        error = "candidate_miss"
                    elif not prediction or prediction.get("action") != "attach" or str(prediction.get("event_id")) not in predicted_ids_by_gold.get(str(key), set()):
                        decision_errors += 1
                        error = "decision_after_recall"
                if prediction:
                    actual_key = bindings.get(index) or str(prediction.get("event_id") or f"create:{step_id}:{index}")
                    predicted_ids_by_gold.setdefault(str(key), set()).add(actual_key)
                else:
                    error = error or "missing_output_or_mention"
                new_seen.add(str(key))
                details.append({"case_id": case.case_id, "step_id": step_id,
                                "mention_index": index, "expected": row,
                                "prediction": prediction, "error": error})
            for index, row in valid_predictions.items():
                node = (case.case_id, str(step_id), index)
                actual_nodes.add(node)
                actual_groups[node] = (case.case_id, bindings.get(index) or
                                       str(row.get("event_id") or f"create:{step_id}:{index}"))
            seen.update(new_seen)
    def pairs(groups):
        nodes = list(groups)
        return {frozenset((left, right)) for i, left in enumerate(nodes) for right in nodes[i + 1:]
                if groups[left] == groups[right]}
    expected_pairs, actual_pairs = pairs(expected_groups), pairs(actual_groups)
    tp = len(expected_nodes & actual_nodes)
    return {
        "membership_precision": tp / len(actual_nodes) if actual_nodes else 0.0 if expected_nodes else None,
        "membership_recall": tp / len(expected_nodes) if expected_nodes else None,
        "membership_note": "mention presence; group relationships and decision fields scored separately",
        "decision_field_accuracy": {field: decision_matches[field] / total for field, total in decision_totals.items()},
        "decision_field_samples": dict(decision_totals),
        "relationship_precision": len(expected_pairs & actual_pairs) / len(actual_pairs) if actual_pairs else None,
        "relationship_recall": len(expected_pairs & actual_pairs) / len(expected_pairs) if expected_pairs else None,
        "false_merge_count": len(actual_pairs - expected_pairs),
        "false_split_count": len(expected_pairs - actual_pairs),
        "candidate_recall": recall_hits / recall_total if recall_total else None,
        "candidate_recall_denominator": recall_total,
        "decision_after_recall_errors": decision_errors,
        "labeled_steps": labeled_steps, "missing_steps": missing_steps,
        "labeled_mentions": len(expected_nodes), "predicted_mentions": len(actual_nodes),
        "details": details,
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
        if "selected_item_ids" in expected:
            rank_scores.append(_ordered_overlap(expected_ids, actual_ids) if result.status == "succeeded" and "selected_item_ids" in actual_plan else 0.0)
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
        if "sections" in expected:
            overlaps.append(_ordered_overlap(expected_ids, actual_ids) if result.status == "succeeded" and "sections" in actual else 0.0)
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


def _end_to_end_metrics(cases):
    message_cases = []
    event_cases = []
    for case, result in cases:
        labeled = case.label_values.get("messages", {})
        predictions = {str(row.get("message_id")): row for row in (result.actual or {}).get("message_results", [])}
        if not isinstance(labeled, dict):
            continue
        event_labels, event_steps = {}, []
        for message_id, expected in labeled.items():
            prediction = predictions.get(str(message_id))
            label_view = case.labels.model_copy(update={"values": expected})
            message_case = case.model_copy(update={"case_id": f"{case.case_id}.{message_id}", "labels": label_view})
            message_result = CaseResult(case_id=message_case.case_id, status="succeeded" if prediction else "failed", actual=prediction)
            message_cases.append((message_case, message_result))
            if "mentions" in expected:
                event_labels[str(message_id)] = {"mentions": expected["mentions"]}
                event_steps.append({"step_id": str(message_id), "status": message_result.status, "actual": prediction})
        if event_labels:
            event_cases.append((case.model_copy(update={"labels": case.labels.model_copy(update={"values": {"steps": event_labels}})}),
                                result.model_copy(update={"actual": {"steps": event_steps}})))
    analysis = [(case.model_copy(update={"labels": case.labels.model_copy(update={"values": case.label_values["message_analysis"]})}), result)
                for case, result in message_cases if "message_analysis" in case.label_values]
    importance = [(case.model_copy(update={"labels": case.labels.model_copy(update={"values": case.label_values["importance"]})}), result)
                  for case, result in message_cases if "importance" in case.label_values]
    return {"message_analysis": _field_metrics(analysis, actual_key="message_analysis") if analysis else None,
            "importance": _importance_metrics(importance) if importance else None,
            "events": _event_metrics(event_cases) if event_cases else None,
            "labeled_messages": len(message_cases),
            "note": "Only explicitly labeled messages/fields are evaluated; flow completion is not quality"}


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


def _sum_number(values: Iterable[float | None]) -> float | None:
    rows = list(values)
    return sum(float(value) for value in rows) if rows and all(value is not None for value in rows) else None


def _sum_optional_int(values: Iterable[int | None]) -> int | None:
    rows = list(values)
    return sum(int(value) for value in rows) if rows and all(value is not None for value in rows) else None


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


def _stage_measurements(cases):
    from app.services.call_metering import summarize_attempts
    rows = {}
    for case in cases:
        if case.cache_hit:
            continue
        for attempt in case.metadata.get("attempts", []):
            rows.setdefault(attempt["stage"], []).append(attempt)
    return {stage: {**summarize_attempts(attempts),
                    "request_duration_sum_ms": sum(row["duration_ms"] for row in attempts)}
            for stage, attempts in rows.items()}
