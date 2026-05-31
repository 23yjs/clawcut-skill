from __future__ import annotations

import math
import statistics
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    from .ark_aesthetic_judge_client import (
        ArkAestheticJudgeConfig,
        ArkAestheticJudgeError,
        call_ark_aesthetic_judge,
    )
    from .aesthetic_judge_prompts import AESTHETIC_JUDGE_PROMPT_VERSION
except ImportError:  # pragma: no cover - script mode
    from ark_aesthetic_judge_client import ArkAestheticJudgeConfig, ArkAestheticJudgeError, call_ark_aesthetic_judge
    from aesthetic_judge_prompts import AESTHETIC_JUDGE_PROMPT_VERSION


SCORE_KEYS = [
    "clip_boundary_completeness",
    "transition_coherence",
    "pacing_and_conciseness",
    "audio_visual_continuity",
    "standalone_watchability",
]


class AestheticJudgeValidationError(ValueError):
    pass


def sanitize_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _round(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def validate_aesthetic_judge_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise AestheticJudgeValidationError("Judge 结果根节点必须是 object")
    scores = result.get("scores")
    if not isinstance(scores, dict):
        raise AestheticJudgeValidationError("Judge 结果缺少 scores object")
    normalized_scores: dict[str, float] = {}
    for key in SCORE_KEYS:
        if key not in scores:
            raise AestheticJudgeValidationError(f"Judge scores 缺少 {key}")
        try:
            score = float(scores[key])
        except (TypeError, ValueError) as exc:
            raise AestheticJudgeValidationError(f"Judge score 必须是数字：{key}") from exc
        if score < 0 or score > 5:
            raise AestheticJudgeValidationError(f"Judge score 超出 0-5：{key}={score}")
        normalized_scores[key] = score
    confidence = result.get("judge_confidence")
    try:
        confidence_float = float(confidence)
    except (TypeError, ValueError) as exc:
        raise AestheticJudgeValidationError("judge_confidence 必须是数字") from exc
    if confidence_float < 0 or confidence_float > 1:
        raise AestheticJudgeValidationError("judge_confidence 必须在 0-1 之间")
    if not isinstance(result.get("issues"), list):
        raise AestheticJudgeValidationError("issues 必须是数组")
    aesthetic_score = 20.0 * (sum(normalized_scores.values()) / len(SCORE_KEYS))
    return {
        **result,
        "judge_version": result.get("judge_version", AESTHETIC_JUDGE_PROMPT_VERSION),
        "judge_status": result.get("judge_status", "scored"),
        "scores": normalized_scores,
        "manual_review_required": bool(result.get("manual_review_required", False)),
        "judge_confidence": _round(confidence_float),
        "aesthetic_score_v1": _round(aesthetic_score),
    }


def summarize_judge_runs(validated_results: list[dict[str, Any]], metadata: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [float(result["aesthetic_score_v1"]) for result in validated_results]
    score_range = max(scores) - min(scores) if scores else 0.0
    score_std = statistics.pstdev(scores) if len(scores) > 1 else 0.0
    median_score = statistics.median(scores) if scores else None
    confidence_values = [float(result.get("judge_confidence", 0)) for result in validated_results]
    manual_required = any(bool(result.get("manual_review_required")) for result in validated_results)
    low_confidence = any(value < 0.50 for value in confidence_values)
    stability_warning = bool(score_range >= 20.0)
    return {
        "judge_status": "scored",
        "judge_version": AESTHETIC_JUDGE_PROMPT_VERSION,
        "judge_repeats": len(validated_results),
        "judge_scores": [_round(score) for score in scores],
        "judge_score_median": _round(median_score),
        "judge_score_min": _round(min(scores)) if scores else None,
        "judge_score_max": _round(max(scores)) if scores else None,
        "judge_score_range": _round(score_range),
        "judge_score_std": _round(score_std),
        "judge_stability_warning": stability_warning,
        "judge_manual_review_required": manual_required or low_confidence,
        "aesthetic_score_v1": _round(median_score),
        "judge_confidence": _round(min(confidence_values)) if confidence_values else None,
        "judge_results": validated_results,
        "judge_metadata": metadata,
    }


def run_aesthetic_judge(
    *,
    judge_video_url: str,
    instruction: str,
    video_type: str,
    target_duration: float | None,
    rendered_duration: float | None,
    config: ArkAestheticJudgeConfig,
    repeats: int = 1,
) -> dict[str, Any]:
    if repeats <= 0:
        raise ValueError("judge_repeats 必须为正整数")
    validated_results: list[dict[str, Any]] = []
    metadata_items: list[dict[str, Any]] = []
    for _ in range(repeats):
        raw_result, metadata = call_ark_aesthetic_judge(
            judge_video_url=judge_video_url,
            instruction=instruction,
            video_type=video_type,
            target_duration=target_duration,
            rendered_duration=rendered_duration,
            config=config,
        )
        validated_results.append(validate_aesthetic_judge_result(raw_result))
        metadata_items.append(metadata)
    return summarize_judge_runs(validated_results, metadata_items)
