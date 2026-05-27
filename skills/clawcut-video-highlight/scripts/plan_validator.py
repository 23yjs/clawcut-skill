from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # pragma: no cover - exercised only without optional dependency.
    Draft202012Validator = None

from utils import SKILL_DIR, SkillError, load_config, read_json


SCHEMA_PATH = SKILL_DIR / "schemas" / "edit_plan.schema.json"


def _segment_duration(segment: dict[str, Any]) -> float:
    return float(segment["end"]) - float(segment["start"])


def validate_plan(
    plan: dict[str, Any],
    video_duration: float,
    target_duration: float,
    config: dict,
    schema_path: Path = SCHEMA_PATH,
) -> dict[str, Any]:
    errors: list[str] = []
    if Draft202012Validator is not None:
        schema = read_json(schema_path)
        schema_errors = sorted(Draft202012Validator(schema).iter_errors(plan), key=lambda error: error.path)
        errors.extend(f"schema: {error.message}" for error in schema_errors)
    else:
        for required_field in (
            "video_type",
            "highlight_definition",
            "chunking_strategy",
            "chunks",
            "final_segments",
            "overall_rationale",
        ):
            if required_field not in plan:
                errors.append(f"schema: 缺少必填字段 '{required_field}'")
    warnings: list[str] = []

    validation_config = config.get("validation", {})
    min_segment_duration = float(validation_config.get("min_segment_duration", 1.0))
    tolerance_seconds = float(validation_config.get("target_tolerance_seconds", 2.0))
    tolerance_ratio = float(validation_config.get("target_tolerance_ratio", 0.2))
    max_overlap = float(validation_config.get("max_overlap_seconds", 0.25))
    if float(target_duration) <= 0:
        errors.append("target_duration 必须为正数")
    effective_target_duration = min(float(target_duration), float(video_duration))
    target_tolerance = max(tolerance_seconds, effective_target_duration * tolerance_ratio)

    segments = plan.get("final_segments", [])
    if not segments:
        errors.append("final_segments 不能为空")

    normalized_segments = []
    for index, segment in enumerate(segments):
        for required_field in ("title", "role", "reason"):
            if not str(segment.get(required_field, "")).strip():
                errors.append(f"片段 {index} 缺少必填字段 {required_field}")
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"片段 {index} 的 start/end 无效")
            continue

        if start >= end:
            errors.append(f"片段 {index} 必须满足 start < end")
        if start < 0 or end > video_duration:
            errors.append(f"片段 {index} 超出视频时长范围 0..{video_duration:.3f}")
        if end - start < min_segment_duration:
            errors.append(
                f"片段 {index} 过短：{end - start:.3f} 秒 < {min_segment_duration:.3f} 秒"
            )
        normalized_segments.append({"index": index, "start": start, "end": end})

    normalized_segments.sort(key=lambda item: item["start"])
    for previous, current in zip(normalized_segments, normalized_segments[1:]):
        overlap = previous["end"] - current["start"]
        if overlap > max_overlap:
            errors.append(
                f"片段 {previous['index']} 和片段 {current['index']} 重叠过多：{overlap:.3f} 秒"
            )

    total_duration = sum(max(0.0, _segment_duration(segment)) for segment in segments)
    duration_delta = abs(total_duration - effective_target_duration)
    if duration_delta > target_tolerance:
        errors.append(
            f"片段总时长 {total_duration:.3f} 秒不接近可实现目标 "
            f"{effective_target_duration:.3f} 秒；容忍误差为 {target_tolerance:.3f} 秒"
        )

    if float(target_duration) > video_duration:
        warnings.append("target_duration 大于原视频时长；mock 规划器已自动按原视频时长降级处理")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "total_duration": round(total_duration, 3),
        "target_duration": float(target_duration),
        "effective_target_duration": round(effective_target_duration, 3),
        "duration_delta": round(duration_delta, 3),
    }


def assert_valid_plan(plan: dict[str, Any], video_duration: float, target_duration: float, config: dict) -> dict[str, Any]:
    result = validate_plan(plan, video_duration, target_duration, config)
    if not result["ok"]:
        raise SkillError("剪辑方案校验失败：\n" + "\n".join(result["errors"]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="校验结构化剪辑方案。")
    parser.add_argument("plan_json", type=Path)
    parser.add_argument("--video_duration", type=float, required=True)
    parser.add_argument("--target_duration", type=float, required=True)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    plan = read_json(args.plan_json)
    result = validate_plan(plan, args.video_duration, args.target_duration, config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
