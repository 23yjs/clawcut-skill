from __future__ import annotations

from typing import Any


def compute_plan_metrics(plan: dict[str, Any], target_duration: float) -> dict[str, Any]:
    segments = plan.get("final_segments", [])
    durations = [max(0.0, float(segment["end"]) - float(segment["start"])) for segment in segments]
    total_duration = sum(durations)
    return {
        "segment_count": len(segments),
        "total_duration": round(total_duration, 3),
        "target_duration": float(target_duration),
        "duration_delta": round(abs(total_duration - float(target_duration)), 3),
        "min_segment_duration": round(min(durations), 3) if durations else 0.0,
        "max_segment_duration": round(max(durations), 3) if durations else 0.0,
    }
