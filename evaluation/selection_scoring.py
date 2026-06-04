from __future__ import annotations

from typing import Any

try:
    from .interval_utils import intervals_duration, normalize_intervals, overlap_duration_between
except ImportError:  # pragma: no cover - script mode
    from interval_utils import intervals_duration, normalize_intervals, overlap_duration_between


DEFAULT_HIGHLIGHT_VALUE_WEIGHTS = {
    1: 0.00,
    2: 0.10,
    3: 0.35,
    4: 0.70,
    5: 1.00,
}


def _round(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _compute_f1(precision: float, recall: float) -> float:
    if precision + recall <= 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _segments_by_id(semantic_segments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(segment.get("segment_id")): segment for segment in semantic_segments}


def _intervals_for_ids(semantic_segments: list[dict[str, Any]], segment_ids: list[str]) -> list[dict[str, float]]:
    by_id = _segments_by_id(semantic_segments)
    intervals = []
    for segment_id in segment_ids:
        segment = by_id.get(str(segment_id))
        if segment:
            intervals.append({"start": float(segment["start"]), "end": float(segment["end"])})
    return normalize_intervals(intervals)


def _segments_for_ids(semantic_segments: list[dict[str, Any]], segment_ids: list[str]) -> list[dict[str, Any]]:
    by_id = _segments_by_id(semantic_segments)
    return [by_id[str(segment_id)] for segment_id in segment_ids if str(segment_id) in by_id]


def _pred_intervals(pred_segments: list[dict[str, Any]]) -> list[dict[str, float]]:
    return normalize_intervals({"start": segment["start"], "end": segment["end"]} for segment in pred_segments)


def _weighted_value_for_intervals(
    intervals: list[dict[str, float]],
    semantic_segments: list[dict[str, Any]],
    *,
    min_score: int = 1,
) -> float:
    value = 0.0
    for segment in semantic_segments:
        score = int(segment["default_highlight_score"])
        if score < min_score:
            continue
        weight = 0.0 if bool(segment.get("avoid_by_default")) else DEFAULT_HIGHLIGHT_VALUE_WEIGHTS.get(score, 0.0)
        if weight <= 0:
            continue
        overlap = overlap_duration_between(intervals, [{"start": segment["start"], "end": segment["end"]}])
        value += overlap * weight
    return value


def _top_weighted_value(
    pieces: list[tuple[float, float]],
    budget: float,
) -> float:
    remaining = max(0.0, float(budget))
    total = 0.0
    for duration, weight in sorted(pieces, key=lambda item: item[1], reverse=True):
        if remaining <= 0:
            break
        used = min(duration, remaining)
        total += used * weight
        remaining -= used
    return total


def _optimal_generic_value(
    semantic_segments: list[dict[str, Any]],
    duration_budget: float,
) -> float:
    pieces = []
    for segment in semantic_segments:
        weight = 0.0 if bool(segment.get("avoid_by_default")) else DEFAULT_HIGHLIGHT_VALUE_WEIGHTS.get(int(segment["default_highlight_score"]), 0.0)
        duration = float(segment["end"]) - float(segment["start"])
        if duration > 0 and weight > 0:
            pieces.append((duration, weight))
    return _top_weighted_value(pieces, duration_budget)


def _total_generic_value(semantic_segments: list[dict[str, Any]], *, min_score: int = 1) -> float:
    value = 0.0
    for segment in semantic_segments:
        score = int(segment["default_highlight_score"])
        if score < min_score:
            continue
        weight = 0.0 if bool(segment.get("avoid_by_default")) else DEFAULT_HIGHLIGHT_VALUE_WEIGHTS.get(score, 0.0)
        duration = float(segment["end"]) - float(segment["start"])
        if duration > 0 and weight > 0:
            value += duration * weight
    return value


def _actual_generic_value(
    pred_intervals: list[dict[str, float]],
    semantic_segments: list[dict[str, Any]],
    duration_budget: float,
) -> float:
    pieces = []
    for segment in semantic_segments:
        weight = 0.0 if bool(segment.get("avoid_by_default")) else DEFAULT_HIGHLIGHT_VALUE_WEIGHTS.get(int(segment["default_highlight_score"]), 0.0)
        if weight <= 0:
            continue
        overlap_duration = overlap_duration_between(pred_intervals, [{"start": segment["start"], "end": segment["end"]}])
        if overlap_duration > 0:
            pieces.append((overlap_duration, weight))
    return _top_weighted_value(pieces, duration_budget)


def compute_generic_selection_score(
    pred_segments: list[dict[str, Any]],
    semantic_segments: list[dict[str, Any]],
    *,
    duration_budget: float | None,
    duration_score: float,
    required_highlight_segment_ids: list[str] | None = None,
    allowed_context_segment_ids: list[str] | None = None,
) -> dict[str, Any]:
    pred_intervals = _pred_intervals(pred_segments)
    pred_total_duration = intervals_duration(pred_intervals)
    required_highlight_segment_ids = required_highlight_segment_ids or []
    allowed_context_segment_ids = allowed_context_segment_ids or []
    default_highlight_intervals = [
        {"start": float(segment["start"]), "end": float(segment["end"])}
        for segment in semantic_segments
        if int(segment["default_highlight_score"]) >= 4
        and not bool(segment.get("avoid_by_default"))
    ]
    if required_highlight_segment_ids:
        generic_target_source = "resolver"
        required_segments = _segments_for_ids(semantic_segments, required_highlight_segment_ids)
        required_intervals = _intervals_for_ids(semantic_segments, required_highlight_segment_ids)
        acceptable_intervals = _intervals_for_ids(
            semantic_segments,
            required_highlight_segment_ids + allowed_context_segment_ids,
        )
    else:
        generic_target_source = "legacy_threshold_fallback"
        required_segments = [
            segment
            for segment in semantic_segments
            if int(segment["default_highlight_score"]) >= 4
            and not bool(segment.get("avoid_by_default"))
        ]
        required_intervals = default_highlight_intervals
        acceptable_intervals = required_intervals

    if duration_budget is None:
        generic_value_mode = "full_gt_required"
        generic_value_optimal = _total_generic_value(required_segments)
        generic_value_actual = _weighted_value_for_intervals(pred_intervals, required_segments)
    else:
        generic_value_mode = "budgeted"
        duration_budget = float(duration_budget)
        generic_value_optimal = _optimal_generic_value(required_segments, duration_budget)
        generic_value_actual = _actual_generic_value(pred_intervals, required_segments, duration_budget)
    generic_value_score = generic_value_actual / generic_value_optimal if generic_value_optimal > 0 else 0.0
    default_highlight_duration = overlap_duration_between(pred_intervals, default_highlight_intervals)
    default_highlight_precision = (
        default_highlight_duration / pred_total_duration
        if pred_total_duration > 0
        else 0.0
    )
    acceptable_overlap_duration = overlap_duration_between(pred_intervals, acceptable_intervals)
    acceptable_precision = (
        acceptable_overlap_duration / pred_total_duration
        if pred_total_duration > 0
        else 0.0
    )
    generic_core_score = _compute_f1(generic_value_score, acceptable_precision)
    avoid_intervals = [
        {"start": float(segment["start"]), "end": float(segment["end"])}
        for segment in semantic_segments
        if bool(segment.get("avoid_by_default"))
    ]
    avoid_overlap = overlap_duration_between(pred_intervals, avoid_intervals)
    avoid_ratio = avoid_overlap / pred_total_duration if pred_total_duration > 0 else 0.0
    avoid_compliance = max(0.0, 1.0 - avoid_ratio)
    selection_score = 100.0 * generic_core_score * avoid_compliance * float(duration_score)
    return {
        "score_type": "generic",
        "pred_total_duration": _round(pred_total_duration),
        "generic_value_actual": _round(generic_value_actual),
        "generic_value_optimal": _round(generic_value_optimal),
        "generic_value_score": _round(generic_value_score),
        "generic_value_mode": generic_value_mode,
        "generic_target_source": generic_target_source,
        "required_highlight_segment_ids": list(required_highlight_segment_ids),
        "allowed_context_segment_ids": list(allowed_context_segment_ids),
        "default_highlight_duration": _round(default_highlight_duration),
        "default_highlight_precision": _round(default_highlight_precision),
        "acceptable_overlap_duration": _round(acceptable_overlap_duration),
        "acceptable_precision": _round(acceptable_precision),
        "generic_core_score": _round(generic_core_score),
        "avoid_by_default_overlap_duration": _round(avoid_overlap),
        "avoid_by_default_overlap_ratio": _round(avoid_ratio),
        "default_avoid_compliance_score": _round(avoid_compliance),
        "duration_score": _round(duration_score),
        "selection_score_v1": _round(selection_score),
    }


def compute_guided_selection_score(
    pred_segments: list[dict[str, Any]],
    semantic_segments: list[dict[str, Any]],
    *,
    relevant_segment_ids: list[str],
    forbidden_segment_ids: list[str],
    allowed_context_segment_ids: list[str] | None = None,
    selection_scope: str,
    duration_budget: float | None,
    duration_score: float,
) -> dict[str, Any]:
    pred_intervals = _pred_intervals(pred_segments)
    pred_total_duration = intervals_duration(pred_intervals)
    allowed_context_segment_ids = allowed_context_segment_ids or []
    relevant_intervals = _intervals_for_ids(semantic_segments, relevant_segment_ids)
    allowed_context_intervals = _intervals_for_ids(semantic_segments, allowed_context_segment_ids)
    acceptable_intervals = normalize_intervals(relevant_intervals + allowed_context_intervals)
    forbidden_intervals = _intervals_for_ids(semantic_segments, forbidden_segment_ids)
    relevant_gt_total_duration = intervals_duration(relevant_intervals)
    matched_relevant_duration = overlap_duration_between(pred_intervals, relevant_intervals)
    precision = matched_relevant_duration / pred_total_duration if pred_total_duration > 0 else 0.0
    allowed_context_overlap_duration = overlap_duration_between(pred_intervals, allowed_context_intervals)
    acceptable_overlap_duration = overlap_duration_between(pred_intervals, acceptable_intervals)
    acceptable_precision = acceptable_overlap_duration / pred_total_duration if pred_total_duration > 0 else 0.0
    if duration_budget is None:
        coverage_mode = "full_gt"
        coverage_denominator = relevant_gt_total_duration
    else:
        coverage_mode = "budgeted"
        coverage_denominator = min(relevant_gt_total_duration, float(duration_budget))
    coverage = min(matched_relevant_duration, coverage_denominator) / coverage_denominator if coverage_denominator > 0 else 0.0
    relevant_f1 = _compute_f1(precision, coverage)
    acceptable_f1 = _compute_f1(acceptable_precision, coverage)
    non_relevant_duration = max(0.0, pred_total_duration - matched_relevant_duration)
    non_relevant_ratio = non_relevant_duration / pred_total_duration if pred_total_duration > 0 else 0.0
    forbidden_overlap = overlap_duration_between(pred_intervals, forbidden_intervals)
    forbidden_ratio = forbidden_overlap / pred_total_duration if pred_total_duration > 0 else 0.0
    forbidden_compliance = max(0.0, 1.0 - forbidden_ratio)
    if selection_scope == "exclusive":
        guided_core_score = acceptable_f1
    else:
        guided_core_score = 0.70 * coverage + 0.30 * acceptable_precision
    selection_score = 100.0 * guided_core_score * forbidden_compliance * float(duration_score)
    return {
        "score_type": "guided",
        "selection_scope": selection_scope,
        "pred_total_duration": _round(pred_total_duration),
        "relevant_gt_total_duration": _round(relevant_gt_total_duration),
        "matched_relevant_duration": _round(matched_relevant_duration),
        "relevant_duration_precision": _round(precision),
        "relevant_duration_coverage": _round(coverage),
        "relevant_duration_f1": _round(relevant_f1),
        "allowed_context_segment_ids": list(allowed_context_segment_ids),
        "allowed_context_overlap_duration": _round(allowed_context_overlap_duration),
        "acceptable_overlap_duration": _round(acceptable_overlap_duration),
        "acceptable_precision": _round(acceptable_precision),
        "coverage_mode": coverage_mode,
        "non_relevant_duration": _round(non_relevant_duration),
        "non_relevant_duration_ratio": _round(non_relevant_ratio),
        "forbidden_overlap_duration": _round(forbidden_overlap),
        "forbidden_duration_ratio": _round(forbidden_ratio),
        "forbidden_compliance_score": _round(forbidden_compliance),
        "guided_core_score": _round(guided_core_score),
        "duration_score": _round(duration_score),
        "selection_score_v1": _round(selection_score),
    }
