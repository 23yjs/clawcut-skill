from __future__ import annotations

from typing import Any


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
    durations = [max(0.0, float(segment["end"]) - float(segment["start"])) for segment in segments]
    total_duration = sum(durations)
    return {
        "segment_count": len(segments),
        "total_duration": round(total_duration, 3),
        "target_duration": resolved_target_duration,
        "duration_delta": round(abs(total_duration - resolved_target_duration), 3),
        "min_segment_duration": round(min(durations), 3) if durations else 0.0,
        "max_segment_duration": round(max(durations), 3) if durations else 0.0,
    }
