from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # pragma: no cover - exercised only without optional dependency.
    Draft202012Validator = None

from utils import SKILL_DIR, SkillError, load_config, read_json


SCHEMA_PATH = SKILL_DIR / "schemas" / "edit_plan.schema.json"


def is_compact_edit_plan(plan: dict[str, Any]) -> bool:
    return (
        isinstance(plan, dict)
        and isinstance(plan.get("final_segments"), list)
        and "chunks" not in plan
        and "chunk_reviews" not in plan
    )


def _segment_duration(segment: dict[str, Any]) -> float:
    return float(segment["end"]) - float(segment["start"])


def _resolve_selected_target_duration(
    plan: dict[str, Any],
    target_duration: float | None,
    video_duration: float,
    config: dict,
    errors: list[str],
) -> tuple[float, dict[str, Any]]:
    duration_policy = plan.get("duration_policy")
    if isinstance(duration_policy, dict):
        mode = str(duration_policy.get("duration_policy_mode", "bounded_auto") or "bounded_auto")
        if mode == "llm_free" and not bool(duration_policy.get("user_specified_duration")):
            try:
                selected_target_duration = float(duration_policy.get("selected_target_duration") or 0)
            except (TypeError, ValueError):
                selected_target_duration = 0.0
                errors.append("llm_free 模式下 duration_policy.selected_target_duration 无效")
            if selected_target_duration <= 0:
                errors.append("llm_free 模式下 duration_policy.selected_target_duration 必须大于 0")
            if selected_target_duration > video_duration:
                errors.append("llm_free 模式下 duration_policy.selected_target_duration 不得超过原视频时长")
            return selected_target_duration, duration_policy
        try:
            selected_target_duration = float(duration_policy["selected_target_duration"])
        except (KeyError, TypeError, ValueError):
            selected_target_duration = float(target_duration or video_duration)
            errors.append("duration_policy.selected_target_duration 无效")
        try:
            allowed_min = float(duration_policy["allowed_min_duration"])
            allowed_max = float(duration_policy["allowed_max_duration"])
            if allowed_min > allowed_max:
                errors.append("duration_policy.allowed_min_duration 不能大于 allowed_max_duration")
            if selected_target_duration < allowed_min or selected_target_duration > allowed_max:
                errors.append(
                    "duration_policy.selected_target_duration 超出 allowed_min_duration/allowed_max_duration 范围"
                )
        except (KeyError, TypeError, ValueError):
            errors.append("duration_policy.allowed_min_duration 或 allowed_max_duration 无效")
        return selected_target_duration, duration_policy

    configured_policy = config.get("duration_policy")
    if isinstance(configured_policy, dict) and configured_policy.get("selected_target_duration") is not None:
        return float(configured_policy["selected_target_duration"]), configured_policy
    if target_duration is not None:
        return float(target_duration), {}
    return min(float(video_duration), 30.0), {}


def _compat_schema_plan(
    plan: dict[str, Any],
    target_duration: float | None,
    video_duration: float,
    config: dict,
) -> dict[str, Any]:
    schema_plan = copy.deepcopy(plan)
    if "duration_policy" not in schema_plan:
        configured_policy = config.get("duration_policy")
        if isinstance(configured_policy, dict):
            schema_plan["duration_policy"] = configured_policy
        else:
            fallback_target = float(target_duration if target_duration is not None else min(video_duration, 30.0))
            schema_plan["duration_policy"] = {
                "duration_policy_mode": "bounded_auto",
                "user_specified_duration": target_duration is not None,
                "user_target_duration": float(target_duration) if target_duration is not None else None,
                "recommended_duration": None,
                "selected_target_duration": fallback_target,
                "allowed_min_duration": fallback_target,
                "allowed_max_duration": fallback_target,
                "final_total_duration": None,
                "duration_policy_reason": "兼容旧版 plan：原始 JSON 缺少 duration_policy。",
            }
    schema_plan.setdefault("excluded_highlights", [])
    return schema_plan


def _compact_limits(config: dict) -> tuple[int, int]:
    planning = config.get("planning") if isinstance(config.get("planning"), dict) else {}
    return int(planning.get("max_final_segments", 24)), int(planning.get("max_title_chars", 30))


def _normalize_compact_plan(plan: dict[str, Any], config: dict, warnings: list[str]) -> None:
    max_segments, max_title_chars = _compact_limits(config)
    plan["edit_plan_schema_version"] = "compact_edit_plan_v2"
    highlight_definition = plan.setdefault("highlight_definition", {})
    if isinstance(highlight_definition, dict):
        if "must_include" in highlight_definition and "must_keep" not in highlight_definition:
            highlight_definition["must_keep"] = highlight_definition.get("must_include")
        highlight_definition.setdefault("must_keep", [])
        highlight_definition.setdefault("avoid", [])
    plan.setdefault("excluded_highlights", [])
    plan.setdefault("self_check", {"pass": True, "issues": []})
    plan.setdefault("chunking_strategy_effective", "compact_edit_plan_v2")
    plan.setdefault("overall_rationale", "compact_edit_plan_v2: 模型仅输出最终片段，Python 负责运行时元数据。")
    if not isinstance(plan.get("duration_policy"), dict):
        configured_policy = config.get("duration_policy") if isinstance(config.get("duration_policy"), dict) else {}
        if str(configured_policy.get("duration_policy_mode") or configured_policy.get("mode")) == "llm_free":
            total_duration = sum(
                max(0.0, float(segment.get("end", 0)) - float(segment.get("start", 0)))
                for segment in plan.get("final_segments", [])
                if isinstance(segment, dict)
            )
            plan["duration_policy"] = {
                "duration_policy_mode": "llm_free",
                "user_specified_duration": False,
                "user_target_duration": None,
                "recommended_duration": None,
                "selected_target_duration": round(total_duration, 3) if total_duration > 0 else None,
                "allowed_min_duration": 0.001,
                "allowed_max_duration": None,
                "final_total_duration": round(total_duration, 3),
                "duration_policy_reason": "compact_edit_plan_v2：按 final_segments 实际总时长补齐。",
            }
    for segment in plan.get("final_segments", [])[:max_segments]:
        title = str(segment.get("title", "") or "").strip()
        if len(title) > max_title_chars:
            segment["title"] = title[:max_title_chars]
            warnings.append(f"片段标题超过 {max_title_chars} 字符，已截断")


def _target_tolerance(selected_target_duration: float) -> float:
    if selected_target_duration <= 30:
        return 3.0
    return selected_target_duration * 0.1


def validate_plan(
    plan: dict[str, Any],
    video_duration: float,
    target_duration: float | None,
    config: dict,
    schema_path: Path = SCHEMA_PATH,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    compact_plan = is_compact_edit_plan(plan)
    if compact_plan:
        _normalize_compact_plan(plan, config, warnings)
    if Draft202012Validator is not None and not compact_plan:
        schema = read_json(schema_path)
        schema_plan = _compat_schema_plan(plan, target_duration, video_duration, config)
        schema_errors = sorted(Draft202012Validator(schema).iter_errors(schema_plan), key=lambda error: error.path)
        errors.extend(f"schema: {error.message}" for error in schema_errors)
    elif Draft202012Validator is None and not compact_plan:
        for required_field in (
            "video_type",
            "duration_policy",
            "highlight_definition",
            "chunking_strategy",
            "chunks",
            "chunk_reviews",
            "final_segments",
            "excluded_highlights",
            "self_check",
            "overall_rationale",
        ):
            if required_field not in plan:
                errors.append(f"schema: 缺少必填字段 '{required_field}'")
    else:
        for required_field in ("video_type", "video_type_reason", "highlight_definition", "final_segments"):
            if required_field not in plan:
                errors.append(f"schema: 缺少必填字段 '{required_field}'")

    validation_config = config.get("validation", {})
    min_segment_duration = float(validation_config.get("min_segment_duration", 1.0))
    max_overlap = float(validation_config.get("max_overlap_seconds", 0.25))
    if target_duration is not None and float(target_duration) <= 0:
        errors.append("target_duration 必须为正数")
    selected_target_duration, duration_policy = _resolve_selected_target_duration(
        plan,
        target_duration,
        video_duration,
        config,
        errors,
    )
    effective_target_duration = selected_target_duration
    target_tolerance = _target_tolerance(effective_target_duration)

    max_final_segments, max_title_chars = _compact_limits(config)
    if compact_plan:
        if len(plan.get("final_segments", [])) > max_final_segments:
            errors.append(f"final_segments 数量不能超过 {max_final_segments}")
        if len(str(plan.get("video_type_reason", "") or "")) > 80:
            errors.append("video_type_reason 不能超过 80 字符")
        highlight_definition = plan.get("highlight_definition")
        if not isinstance(highlight_definition, dict):
            errors.append("highlight_definition 必须是 object")
            highlight_definition = {}
        for field in ("must_keep", "avoid"):
            values = highlight_definition.get(field)
            if not isinstance(values, list):
                errors.append(f"highlight_definition.{field} 必须是数组")
                continue
            if len(values) > 5:
                errors.append(f"highlight_definition.{field} 不能超过 5 条")
            for index, value in enumerate(values):
                if not str(value or "").strip():
                    errors.append(f"highlight_definition.{field}[{index}] 不能为空")
                if len(str(value)) > 30:
                    errors.append(f"highlight_definition.{field}[{index}] 不能超过 30 字符")

    chunks = plan.get("chunks", [])
    chunk_ids = {str(chunk.get("id")) for chunk in chunks if chunk.get("id") is not None}
    chunk_reviews = plan.get("chunk_reviews", [])
    reviews_by_chunk_id = {
        str(review.get("chunk_id")): review
        for review in chunk_reviews
        if review.get("chunk_id") is not None
    }
    if isinstance(plan.get("self_check"), dict) and plan["self_check"].get("pass") is False:
        warnings.append("模型 self_check 未通过：" + "；".join(plan["self_check"].get("issues", [])))

    segments = plan.get("final_segments", [])
    if not segments:
        errors.append("final_segments 不能为空")

    normalized_segments = []
    for index, segment in enumerate(segments):
        required_segment_fields = ("title",) if compact_plan else ("title", "role", "source_chunk_id", "reason")
        for required_field in required_segment_fields:
            if not str(segment.get(required_field, "")).strip():
                errors.append(f"片段 {index} 缺少必填字段 {required_field}")
        if compact_plan and len(str(segment.get("title", "") or "")) > max_title_chars:
            errors.append(f"片段 {index} title 不能超过 {max_title_chars} 字符")
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
        source_chunk_id = str(segment.get("source_chunk_id", ""))
        if (not compact_plan) and source_chunk_id and source_chunk_id not in chunk_ids:
            errors.append(f"片段 {index} 的 source_chunk_id 不存在于 chunks：{source_chunk_id}")
        review = reviews_by_chunk_id.get(source_chunk_id)
        if (not compact_plan) and review:
            try:
                refined_start = float(review["refined_start"])
                refined_end = float(review["refined_end"])
                if abs(start - refined_start) > 1.0 or abs(end - refined_end) > 1.0:
                    warnings.append(
                        f"片段 {index} 的 start/end 与 chunk_review refined_start/refined_end 差异较大："
                        f"segment=({start:.3f},{end:.3f}), refined=({refined_start:.3f},{refined_end:.3f})"
                    )
            except (KeyError, TypeError, ValueError):
                warnings.append(f"片段 {index} 对应的 chunk_review refined_start/refined_end 无法解析")
        elif (not compact_plan) and source_chunk_id:
            warnings.append(f"片段 {index} 找不到对应 chunk_review：{source_chunk_id}")
        normalized_segments.append({"index": index, "start": start, "end": end})

    normalized_segments.sort(key=lambda item: item["start"])
    for previous, current in zip(normalized_segments, normalized_segments[1:]):
        overlap = previous["end"] - current["start"]
        if overlap > max_overlap:
            errors.append(
                f"片段 {previous['index']} 和片段 {current['index']} 重叠过多：{overlap:.3f} 秒"
            )

    excluded_highlights = plan.get("excluded_highlights", [])
    if not isinstance(excluded_highlights, list):
        errors.append("excluded_highlights 必须是数组")
        excluded_highlights = []
    for index, highlight in enumerate(excluded_highlights):
        if not isinstance(highlight, dict):
            errors.append(f"excluded_highlights {index} 必须是 object")
            continue
        try:
            start = float(highlight["start"])
            end = float(highlight["end"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"excluded_highlights {index} 的 start/end 无效")
            continue
        if start >= end:
            warnings.append(f"excluded_highlights {index} 未满足 start < end，已仅作为解释信息保留")
        if start < 0 or end > video_duration:
            warnings.append(f"excluded_highlights {index} 超出视频时长范围，已仅作为解释信息保留")
        for segment in normalized_segments:
            overlap = min(end, segment["end"]) - max(start, segment["start"])
            if overlap > max_overlap:
                warnings.append(
                    f"excluded_highlights {index} 与 final_segments 片段 {segment['index']} 有重叠，"
                    "请确认它不是实际裁剪片段"
                )
                break

    total_duration = sum(max(0.0, _segment_duration(segment)) for segment in segments)
    duration_delta = abs(total_duration - effective_target_duration)
    is_llm_free = (
        isinstance(duration_policy, dict)
        and str(duration_policy.get("duration_policy_mode", "")) == "llm_free"
        and not bool(duration_policy.get("user_specified_duration"))
    )
    if is_llm_free and total_duration <= 0:
        errors.append("llm_free 模式下 final_segments 总时长必须大于 0")
    if is_llm_free and total_duration > video_duration:
        errors.append("llm_free 模式下 final_segments 总时长不得超过原视频时长")
    if (not is_llm_free) and duration_delta > target_tolerance:
        errors.append(
            f"片段总时长 {total_duration:.3f} 秒不接近可实现目标 "
            f"{effective_target_duration:.3f} 秒；容忍误差为 {target_tolerance:.3f} 秒"
        )

    if effective_target_duration > video_duration:
        warnings.append("selected_target_duration 大于原视频时长，最终剪辑可能无法达到该目标")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "total_duration": round(total_duration, 3),
        "target_duration": float(target_duration) if target_duration is not None else None,
        "selected_target_duration": round(selected_target_duration, 3),
        "effective_target_duration": round(effective_target_duration, 3),
        "duration_delta": round(duration_delta, 3),
    }


def assert_valid_plan(plan: dict[str, Any], video_duration: float, target_duration: float | None, config: dict) -> dict[str, Any]:
    result = validate_plan(plan, video_duration, target_duration, config)
    if not result["ok"]:
        raise SkillError("剪辑方案校验失败：\n" + "\n".join(result["errors"]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="校验结构化剪辑方案。")
    parser.add_argument("plan_json", type=Path)
    parser.add_argument("--video_duration", type=float, required=True)
    parser.add_argument("--target_duration", type=float, default=None)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    plan = read_json(args.plan_json)
    result = validate_plan(plan, args.video_duration, args.target_duration, config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
