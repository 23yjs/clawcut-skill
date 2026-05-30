from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_BOUNDARY_TOLERANCE_SECONDS = 1.0


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _round(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[Any] = set()
    for value in values:
        marker = str(value)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def _as_tags(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def segment_duration(segment: dict[str, Any]) -> float:
    return max(0.0, _to_float(segment.get("end")) - _to_float(segment.get("start")))


def overlap_duration(a: dict[str, Any], b: dict[str, Any]) -> float:
    start = max(_to_float(a.get("start")), _to_float(b.get("start")))
    end = min(_to_float(a.get("end")), _to_float(b.get("end")))
    return max(0.0, end - start)


def union_duration(a: dict[str, Any], b: dict[str, Any]) -> float:
    return segment_duration(a) + segment_duration(b) - overlap_duration(a, b)


def temporal_iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    union = union_duration(a, b)
    if union <= 0:
        return 0.0
    return overlap_duration(a, b) / union


def _expand_segment_for_tolerance(
    segment: dict[str, Any],
    boundary_tolerance_seconds: float,
) -> dict[str, Any]:
    if boundary_tolerance_seconds < 0:
        raise ValueError("boundary_tolerance_seconds 不能小于 0")
    expanded = dict(segment)
    expanded["start"] = max(0.0, float(segment["start"]) - boundary_tolerance_seconds)
    expanded["end"] = float(segment["end"]) + boundary_tolerance_seconds
    return expanded


def resolve_target_duration(plan: dict[str, Any], target_duration: float | None = None) -> float:
    duration_policy = plan.get("duration_policy")
    if isinstance(duration_policy, dict) and duration_policy.get("selected_target_duration") is not None:
        return float(duration_policy["selected_target_duration"])
    if target_duration is None:
        return 0.0
    return float(target_duration)


def compute_plan_metrics(plan: dict[str, Any], target_duration: float | None = None) -> dict[str, Any]:
    resolved_target_duration = resolve_target_duration(plan, target_duration)
    segments = plan.get("final_segments", [])
    durations = [segment_duration(segment) for segment in segments]
    total_duration = sum(durations)
    return {
        "segment_count": len(segments),
        "total_duration": _round(total_duration),
        "target_duration": resolved_target_duration,
        "duration_delta": _round(abs(total_duration - resolved_target_duration)),
        "min_segment_duration": _round(min(durations), 3) if durations else 0.0,
        "max_segment_duration": _round(max(durations), 3) if durations else 0.0,
    }


def _is_temporal_match(
    pred: dict[str, Any],
    semantic: dict[str, Any],
    iou_threshold: float,
    overlap_ratio_threshold: float,
    boundary_tolerance_seconds: float = DEFAULT_BOUNDARY_TOLERANCE_SECONDS,
) -> tuple[bool, float, float, float]:
    details = _temporal_match_details(
        pred,
        semantic,
        iou_threshold,
        overlap_ratio_threshold,
        boundary_tolerance_seconds,
    )
    return (
        bool(details["matched"]),
        float(details["raw_iou"]),
        float(details["raw_overlap_duration"]),
        float(details["raw_overlap_ratio"]),
    )


def _temporal_match_details(
    pred: dict[str, Any],
    semantic: dict[str, Any],
    iou_threshold: float,
    overlap_ratio_threshold: float,
    boundary_tolerance_seconds: float = DEFAULT_BOUNDARY_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    raw_overlap = overlap_duration(pred, semantic)
    raw_iou = temporal_iou(pred, semantic)
    raw_min_duration = min(segment_duration(pred), segment_duration(semantic))
    raw_overlap_ratio = raw_overlap / raw_min_duration if raw_min_duration > 0 else 0.0

    tolerant_semantic = _expand_segment_for_tolerance(semantic, boundary_tolerance_seconds)
    tolerant_overlap = overlap_duration(pred, tolerant_semantic)
    tolerant_iou = temporal_iou(pred, tolerant_semantic)
    tolerant_min_duration = min(segment_duration(pred), segment_duration(tolerant_semantic))
    tolerant_overlap_ratio = tolerant_overlap / tolerant_min_duration if tolerant_min_duration > 0 else 0.0

    raw_matched = raw_iou >= iou_threshold or raw_overlap_ratio >= overlap_ratio_threshold
    matched = tolerant_iou >= iou_threshold or tolerant_overlap_ratio >= overlap_ratio_threshold
    if raw_matched:
        matched_by = "raw"
    elif matched:
        matched_by = "boundary_tolerance"
    else:
        matched_by = "none"

    return {
        "matched": matched,
        "matched_by": matched_by,
        "boundary_tolerance_seconds": boundary_tolerance_seconds,
        "raw_iou": raw_iou,
        "raw_overlap_duration": raw_overlap,
        "raw_overlap_ratio": raw_overlap_ratio,
        "tolerant_iou": tolerant_iou,
        "tolerant_overlap_duration": tolerant_overlap,
        "tolerant_overlap_ratio": tolerant_overlap_ratio,
    }


def match_pred_to_semantic_segments(
    pred_segments: list[dict[str, Any]],
    semantic_segments: list[dict[str, Any]],
    iou_threshold: float = 0.1,
    overlap_ratio_threshold: float = 0.3,
    boundary_tolerance_seconds: float = DEFAULT_BOUNDARY_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    matched_segment_ids: list[str] = []
    matched_tags: list[str] = []
    matched_descriptions: list[str] = []
    per_pred_matches: list[dict[str, Any]] = []

    for pred_index, pred in enumerate(pred_segments):
        pred_matches: list[dict[str, Any]] = []
        for semantic in semantic_segments:
            details = _temporal_match_details(
                pred,
                semantic,
                iou_threshold,
                overlap_ratio_threshold,
                boundary_tolerance_seconds,
            )
            if not details["matched"]:
                continue
            segment_id = str(semantic.get("segment_id", ""))
            tags = _as_tags(semantic.get("tags"))
            description = str(semantic.get("description", ""))
            matched_segment_ids.append(segment_id)
            matched_tags.extend(tags)
            if description:
                matched_descriptions.append(description)
            pred_matches.append(
                {
                    "segment_id": segment_id,
                    "start": semantic.get("start"),
                    "end": semantic.get("end"),
                    "description": description,
                    "tags": tags,
                    "default_highlight_score": semantic.get("default_highlight_score"),
                    "avoid_by_default": bool(semantic.get("avoid_by_default")),
                    "iou": _round(details["raw_iou"]),
                    "overlap_duration": _round(details["raw_overlap_duration"]),
                    "overlap_ratio": _round(details["raw_overlap_ratio"]),
                    "tolerant_iou": _round(details["tolerant_iou"]),
                    "tolerant_overlap_duration": _round(details["tolerant_overlap_duration"]),
                    "tolerant_overlap_ratio": _round(details["tolerant_overlap_ratio"]),
                    "matched_by": details["matched_by"],
                    "boundary_tolerance_seconds": boundary_tolerance_seconds,
                }
            )

        per_pred_matches.append(
            {
                "pred_index": pred_index,
                "start": pred.get("start"),
                "end": pred.get("end"),
                "title": pred.get("title", ""),
                "matches": pred_matches,
            }
        )

    return {
        "matched_segment_ids": _unique([item for item in matched_segment_ids if item]),
        "matched_tags": _unique(matched_tags),
        "matched_descriptions": _unique(matched_descriptions),
        "per_pred_matches": per_pred_matches,
        "boundary_tolerance_seconds": boundary_tolerance_seconds,
    }


def _compute_f1(precision: float, recall: float) -> float:
    if precision + recall <= 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def compute_default_highlight_metrics(
    pred_segments: list[dict[str, Any]],
    semantic_segments: list[dict[str, Any]],
    score_threshold: int = 4,
    boundary_tolerance_seconds: float = DEFAULT_BOUNDARY_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    targets = [
        segment
        for segment in semantic_segments
        if _to_float(segment.get("default_highlight_score")) >= score_threshold
        and not bool(segment.get("avoid_by_default"))
    ]
    target_ids = [str(segment.get("segment_id", "")) for segment in targets]
    hit_target_ids: list[str] = []
    best_ious: list[float] = []
    pred_positive_hits = 0
    selected_low_value_segments: list[str] = []

    for pred in pred_segments:
        matched_positive = False
        matched_low_value = False
        for semantic in semantic_segments:
            matched, _iou, _overlap, _ratio = _is_temporal_match(
                pred,
                semantic,
                0.1,
                0.3,
                boundary_tolerance_seconds,
            )
            if not matched:
                continue
            segment_id = str(semantic.get("segment_id", ""))
            is_positive = segment_id in target_ids
            if is_positive:
                matched_positive = True
                hit_target_ids.append(segment_id)
            if bool(semantic.get("avoid_by_default")) or _to_float(semantic.get("default_highlight_score")) < score_threshold:
                matched_low_value = True
                selected_low_value_segments.append(segment_id)
        if matched_positive:
            pred_positive_hits += 1
        if matched_low_value and not matched_positive:
            continue

    for target in targets:
        max_iou = 0.0
        for pred in pred_segments:
            matched, iou, _overlap, _ratio = _is_temporal_match(
                pred,
                target,
                0.1,
                0.3,
                boundary_tolerance_seconds,
            )
            if matched:
                max_iou = max(max_iou, iou)
        if max_iou > 0:
            best_ious.append(max_iou)

    unique_hit_target_ids = _unique([item for item in hit_target_ids if item])
    missed = [segment_id for segment_id in target_ids if segment_id not in unique_hit_target_ids]
    default_highlight_recall = len(unique_hit_target_ids) / len(targets) if targets else 1.0
    default_highlight_precision = pred_positive_hits / len(pred_segments) if pred_segments else 0.0
    return {
        "default_highlight_target_count": len(targets),
        "default_highlight_hit_count": len(unique_hit_target_ids),
        "default_highlight_recall": _round(default_highlight_recall),
        "default_highlight_precision": _round(default_highlight_precision),
        "default_highlight_f1": _round(_compute_f1(default_highlight_precision, default_highlight_recall)),
        "avg_default_highlight_iou": _round(sum(best_ious) / len(best_ious) if best_ious else 0.0),
        "missed_default_highlights": missed,
        "selected_low_value_segments": _unique([item for item in selected_low_value_segments if item]),
        "boundary_tolerance_seconds": boundary_tolerance_seconds,
    }


def compute_segment_reference_metrics(
    pred_segments: list[dict[str, Any]],
    semantic_segments: list[dict[str, Any]],
    relevant_segment_ids: list[str],
    forbidden_segment_ids: list[str],
    boundary_tolerance_seconds: float = DEFAULT_BOUNDARY_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    warnings: list[str] = []
    semantic_by_id = {str(segment.get("segment_id")): segment for segment in semantic_segments}
    relevant_ids = [segment_id for segment_id in relevant_segment_ids if segment_id in semantic_by_id]
    forbidden_ids = [segment_id for segment_id in forbidden_segment_ids if segment_id in semantic_by_id]

    matched_relevant: list[str] = []
    matched_forbidden: list[str] = []
    relevant_prediction_count = 0
    forbidden_prediction_count = 0

    for pred in pred_segments:
        pred_hit_relevant = False
        pred_hit_forbidden = False
        for segment_id in relevant_ids:
            matched, _iou, _overlap, _ratio = _is_temporal_match(
                pred,
                semantic_by_id[segment_id],
                0.1,
                0.3,
                boundary_tolerance_seconds,
            )
            if matched:
                pred_hit_relevant = True
                matched_relevant.append(segment_id)
        for segment_id in forbidden_ids:
            matched, _iou, _overlap, _ratio = _is_temporal_match(
                pred,
                semantic_by_id[segment_id],
                0.1,
                0.3,
                boundary_tolerance_seconds,
            )
            if matched:
                pred_hit_forbidden = True
                matched_forbidden.append(segment_id)
        if pred_hit_relevant:
            relevant_prediction_count += 1
        if pred_hit_forbidden:
            forbidden_prediction_count += 1

    matched_relevant_ids = _unique(matched_relevant)
    matched_forbidden_ids = _unique(matched_forbidden)
    missed_relevant_ids = [segment_id for segment_id in relevant_ids if segment_id not in matched_relevant_ids]

    if not pred_segments:
        warnings.append("pred_segments 为空，precision 和 violation_rate 的分母为 0")
    if not relevant_ids:
        warnings.append("relevant_segment_ids 为空，relevant recall 的分母为 0")

    relevant_precision = relevant_prediction_count / len(pred_segments) if pred_segments else 0.0
    relevant_recall = len(matched_relevant_ids) / len(relevant_ids) if relevant_ids else 0.0
    forbidden_violation_rate = forbidden_prediction_count / len(pred_segments) if pred_segments else 0.0

    return {
        "relevant_segment_ids": relevant_ids,
        "matched_relevant_segment_ids": matched_relevant_ids,
        "missed_relevant_segment_ids": missed_relevant_ids,
        "relevant_prediction_count": relevant_prediction_count,
        "relevant_segment_precision": _round(relevant_precision),
        "relevant_segment_recall": _round(relevant_recall),
        "relevant_segment_f1": _round(_compute_f1(relevant_precision, relevant_recall)),
        "forbidden_segment_ids": forbidden_ids,
        "matched_forbidden_segment_ids": matched_forbidden_ids,
        "forbidden_segment_hit_count": len(matched_forbidden_ids),
        "forbidden_prediction_count": forbidden_prediction_count,
        "forbidden_segment_violation_rate": _round(forbidden_violation_rate),
        "boundary_tolerance_seconds": boundary_tolerance_seconds,
        "warnings": warnings,
    }


def compute_must_cover_tag_coverage(matched_tags: list[str], must_cover_tags: list[str]) -> dict[str, Any]:
    matched_set = set(_as_tags(matched_tags))
    must_cover = _as_tags(must_cover_tags)
    hits = [tag for tag in must_cover if tag in matched_set]
    missed = [tag for tag in must_cover if tag not in matched_set]
    return {
        "must_cover_total": len(must_cover),
        "must_cover_hit": len(hits),
        "must_cover_coverage": _round(len(hits) / len(must_cover) if must_cover else 1.0),
        "missed_must_cover_tags": missed,
    }


def compute_must_avoid_violation(
    matched_tags: list[str],
    must_avoid_tags: list[str],
    per_pred_matches: list[dict[str, Any]],
) -> dict[str, Any]:
    must_avoid = _as_tags(must_avoid_tags)
    matched_set = set(_as_tags(matched_tags))
    violated_tags = [tag for tag in must_avoid if tag in matched_set]
    violating_segments: list[dict[str, Any]] = []

    for pred_match in per_pred_matches:
        segment_tags: list[str] = []
        segment_ids: list[str] = []
        for match in pred_match.get("matches", []):
            tags = _as_tags(match.get("tags"))
            local_violated = [tag for tag in tags if tag in must_avoid]
            if local_violated:
                segment_tags.extend(local_violated)
                segment_ids.append(str(match.get("segment_id", "")))
        if segment_tags:
            violating_segments.append(
                {
                    "pred_index": pred_match.get("pred_index"),
                    "start": pred_match.get("start"),
                    "end": pred_match.get("end"),
                    "violated_tags": _unique(segment_tags),
                    "matched_segment_ids": _unique([item for item in segment_ids if item]),
                }
            )

    return {
        "must_avoid_total": len(must_avoid),
        "violated_tags": _unique(violated_tags),
        "violation_count": len(_unique(violated_tags)),
        "violation_rate": _round(len(_unique(violated_tags)) / len(must_avoid) if must_avoid else 0.0),
        "violating_segments": violating_segments,
    }


def _flatten_attribute_notes(attribute_notes: Any) -> str:
    if not isinstance(attribute_notes, dict):
        return str(attribute_notes or "")
    parts: list[str] = []
    for value in attribute_notes.values():
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value:
            parts.append(str(value))
    return " ".join(parts)


def _extract_keywords(instruction: str) -> list[str]:
    known_terms = [
        "闪钻",
        "蝴蝶结",
        "个性化",
        "装饰",
        "外观",
        "核心卖点",
        "卖点",
        "功能",
        "讲解",
        "水杯",
        "杯套",
        "保温",
        "账号",
        "片尾",
        "结束语",
        "兴奋",
    ]
    stop_words = {
        "剪出",
        "剪",
        "这个视频",
        "视频",
        "片段",
        "高光",
        "时刻",
        "不要",
        "只剪",
        "突出",
        "带",
        "的",
        "和",
        "最",
        "请",
    }
    keywords: list[str] = []
    for term in known_terms:
        if term in instruction:
            keywords.append(term)
    for part in re.split(r"[\s,，。.!！?？、；;：:\"“”'‘’（）()]+", instruction):
        text = part.strip()
        if not text:
            continue
        for stop_word in stop_words:
            text = text.replace(stop_word, "")
        if 2 <= len(text) <= 16 and text not in stop_words:
            keywords.append(text)
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]+", instruction):
        if len(word) >= 2:
            keywords.append(word.lower())
    return _unique([keyword for keyword in keywords if keyword])


def description_mock_judge(
    instruction: str,
    matched_descriptions: list[str],
    semantic_segments: list[dict[str, Any]],
) -> dict[str, Any]:
    keywords = _extract_keywords(instruction)
    evidence: list[dict[str, Any]] = []
    searchable_items: list[tuple[str, str, str]] = []

    for description in matched_descriptions:
        searchable_items.append(("", str(description), str(description)))
    for segment in semantic_segments:
        segment_id = str(segment.get("segment_id", ""))
        text = " ".join(
            [
                str(segment.get("description", "")),
                " ".join(_as_tags(segment.get("tags"))),
                _flatten_attribute_notes(segment.get("attribute_notes")),
            ]
        )
        searchable_items.append((segment_id, text, str(segment.get("description", ""))))

    for keyword in keywords:
        keyword_lower = keyword.lower()
        for segment_id, text, description in searchable_items:
            if keyword_lower in text.lower():
                evidence.append(
                    {
                        "keyword": keyword,
                        "segment_id": segment_id,
                        "evidence": description or text,
                    }
                )
                break

    unique_keywords = set(keywords)
    matched_keywords = {item["keyword"] for item in evidence}
    score = 0.0
    if unique_keywords:
        score = 10.0 * len(matched_keywords) / len(unique_keywords)

    return {
        "semantic_match_score": _round(min(10.0, score)),
        "matched_evidence": evidence,
        "judge_mode": "description_mock_judge",
        "warning": "mock judge, not real LLM",
    }


def compute_duration_metrics(
    result_summary_or_segments: dict[str, Any],
    target_duration_from_case: float | None = None,
) -> dict[str, Any]:
    duration_policy = result_summary_or_segments.get("duration_policy")
    selected_target_duration = None
    if isinstance(duration_policy, dict):
        selected_target_duration = duration_policy.get("selected_target_duration")
    if selected_target_duration is None:
        selected_target_duration = target_duration_from_case
    selected = _to_float(selected_target_duration)

    final_total = result_summary_or_segments.get("final_total_duration")
    if final_total is None:
        final_total = sum(segment_duration(segment) for segment in result_summary_or_segments.get("final_segments", []))
    final_total_duration = _to_float(final_total)

    duration_delta = result_summary_or_segments.get("duration_delta")
    if duration_delta is None:
        duration_delta = abs(final_total_duration - selected) if selected > 0 else 0.0
    duration_delta_float = _to_float(duration_delta)
    tolerance = 3.0 if selected <= 30 else selected * 0.1
    if selected <= 0:
        duration_score = 10.0
    elif duration_delta_float <= tolerance:
        duration_score = 10.0
    else:
        duration_score = max(0.0, 10.0 * (1.0 - (duration_delta_float - tolerance) / max(selected, 1.0)))

    return {
        "selected_target_duration": _round(selected),
        "final_total_duration": _round(final_total_duration),
        "duration_delta": _round(duration_delta_float),
        "duration_tolerance": _round(tolerance),
        "duration_score": _round(duration_score),
    }


def compute_excluded_highlight_summary(excluded_highlights: list[dict[str, Any]]) -> dict[str, Any]:
    reasons = Counter(str(item.get("excluded_reason", "unknown")) for item in excluded_highlights)
    high_score_count = sum(1 for item in excluded_highlights if _to_float(item.get("score")) >= 4.0)
    return {
        "excluded_count": len(excluded_highlights),
        "excluded_reasons": dict(reasons),
        "high_score_excluded_count": high_score_count,
    }


def compute_functional_completeness(output_paths: dict[str, Any]) -> dict[str, Any]:
    result_summary = output_paths.get("result_summary")
    result_status = output_paths.get("result_summary_status")
    if isinstance(result_summary, dict):
        result_status = result_summary.get("status")
    checks = {
        "highlight_video_exists": Path(str(output_paths.get("highlight_video", ""))).exists(),
        "segments_json_exists": Path(str(output_paths.get("segments_json", ""))).exists(),
        "report_md_exists": Path(str(output_paths.get("report_md", ""))).exists(),
        "result_summary_exists": Path(str(output_paths.get("result_summary_path", ""))).exists(),
        "run_log_exists": Path(str(output_paths.get("run_log", ""))).exists(),
        "result_summary_success": result_status == "success",
    }
    score = 10.0 * sum(1 for ok in checks.values() if ok) / len(checks)
    return {
        **checks,
        "functional_completeness_score": _round(score),
    }


def _score_from_ratio(value: Any) -> float:
    return max(0.0, min(10.0, _to_float(value) * 10.0))


def _inverse_score_from_ratio(value: Any) -> float:
    return max(0.0, min(10.0, (1.0 - _to_float(value)) * 10.0))


def _explainability_score(metrics: dict[str, Any]) -> float:
    explicit = metrics.get("explainability_score")
    if explicit is not None:
        return _to_float(explicit)
    functional = metrics.get("functional_completeness", {})
    if isinstance(functional, dict) and functional.get("report_md_exists"):
        return 10.0
    return 5.0


def compute_case_score(case: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    annotation_coverage = str(case.get("annotation_coverage", "covered"))
    judge_mode = str(case.get("judge_mode", ""))
    instruction_type = str(case.get("instruction_type", ""))
    if annotation_coverage == "uncovered" or judge_mode == "manual_only":
        return {
            "final_score": None,
            "evaluation_status": "manual_only",
            "score_components": {},
        }

    must_cover = metrics.get("must_cover", {})
    must_avoid = metrics.get("must_avoid", {})
    default_highlight = metrics.get("default_highlight", {})
    duration = metrics.get("duration", {})
    functional = metrics.get("functional_completeness", {})
    description = metrics.get("description_judge", {})

    coverage_score = _score_from_ratio(must_cover.get("must_cover_coverage", 1.0))
    avoid_score = _inverse_score_from_ratio(must_avoid.get("violation_rate", 0.0))
    default_score = _score_from_ratio(default_highlight.get("default_highlight_recall", 0.0))
    duration_score = _to_float(duration.get("duration_score", 0.0))
    functional_score = _to_float(functional.get("functional_completeness_score", 0.0))
    description_score = _to_float(description.get("semantic_match_score", 0.0))
    explainability_score = _explainability_score(metrics)
    conflict_override_score = 10.0 if _to_float(must_avoid.get("violation_rate", 0.0)) == 0 else 0.0

    if instruction_type == "generic":
        components = {
            "default_highlight_score": (default_score, 0.40),
            "must_avoid": (avoid_score, 0.15),
            "duration": (duration_score, 0.15),
            "functional_completeness": (functional_score, 0.15),
            "editing_report_explainability": (explainability_score, 0.15),
        }
    elif instruction_type == "conflict":
        components = {
            "must_cover": (coverage_score, 0.35),
            "must_avoid": (avoid_score, 0.35),
            "duration": (duration_score, 0.10),
            "functional_completeness": (functional_score, 0.10),
            "conflict_override": (conflict_override_score, 0.10),
        }
    elif annotation_coverage == "partial" or judge_mode == "description_mock_judge":
        components = {
            "must_cover": (coverage_score, 0.25),
            "description_mock_judge": (description_score, 0.35),
            "must_avoid": (avoid_score, 0.15),
            "duration": (duration_score, 0.10),
            "functional_completeness": (functional_score, 0.15),
        }
    elif instruction_type == "abnormal" or judge_mode == "robustness":
        return {
            "final_score": None,
            "evaluation_status": "record_only",
            "score_components": {},
        }
    else:
        auxiliary = description_score if description_score > 0 else min(default_score, coverage_score)
        components = {
            "must_cover": (coverage_score, 0.40),
            "must_avoid": (avoid_score, 0.20),
            "duration": (duration_score, 0.15),
            "functional_completeness": (functional_score, 0.15),
            "description_or_default_auxiliary": (auxiliary, 0.10),
        }

    weighted = sum(score * weight for score, weight in components.values())
    if math.isnan(weighted):
        weighted = 0.0
    return {
        "final_score": _round(weighted * 10.0),
        "evaluation_status": "scored",
        "score_components": {
            name: {"score": _round(score), "weight": weight}
            for name, (score, weight) in components.items()
        },
    }
