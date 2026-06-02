from __future__ import annotations

from typing import Any

try:
    from .ark_resolver_client import ArkResolverConfig, call_ark_resolver
except ImportError:  # pragma: no cover - script mode
    from ark_resolver_client import ArkResolverConfig, call_ark_resolver


class ResolverValidationError(ValueError):
    pass


REQUIRED_FIELDS = {
    "instruction_mode",
    "selection_scope",
    "resolution_status",
    "use_default_highlights",
    "relevant_segment_ids",
    "forbidden_segment_ids",
    "unresolved_requirements",
    "resolver_reason",
}
INSTRUCTION_MODES = {"generic", "specific", "conflict", "unresolved"}
SELECTION_SCOPES = {"not_applicable", "preferential", "exclusive", "unknown"}
RESOLUTION_STATUSES = {"resolved", "partial", "unresolved", "failed"}
DURATION_STATUSES = {"resolved", "not_specified", "unresolved"}
DURATION_SOURCES = {"instruction", "target_duration_argument", "none"}


LEGACY_DURATION_CONSTRAINT = {
    "status": "not_specified",
    "min_seconds": None,
    "max_seconds": None,
    "source": "none",
    "reason": "legacy generated_case 未包含 duration_constraint。",
}


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ResolverValidationError(f"{field} 必须是字符串列表")
    return value


def _reject_duplicates(values: list[str], field: str) -> None:
    if len(values) != len(set(values)):
        raise ResolverValidationError(f"{field} 不能包含重复 segment_id")


def _validate_seconds(value: Any, field: str) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResolverValidationError(f"duration_constraint.{field} 必须是非负 number 或 null")
    if value < 0:
        raise ResolverValidationError(f"duration_constraint.{field} 不能为负数")
    return value


def _validate_duration_constraint(value: Any) -> dict[str, Any]:
    if value is None:
        value = LEGACY_DURATION_CONSTRAINT
    if not isinstance(value, dict):
        raise ResolverValidationError("duration_constraint 必须是 dict")
    required = {"status", "min_seconds", "max_seconds", "source", "reason"}
    missing = sorted(required - set(value.keys()))
    if missing:
        raise ResolverValidationError(f"duration_constraint 缺少字段：{', '.join(missing)}")

    status = value["status"]
    source = value["source"]
    reason = value["reason"]
    min_seconds = _validate_seconds(value["min_seconds"], "min_seconds")
    max_seconds = _validate_seconds(value["max_seconds"], "max_seconds")

    if status not in DURATION_STATUSES:
        raise ResolverValidationError(f"duration_constraint.status 不合法：{status}")
    if source not in DURATION_SOURCES:
        raise ResolverValidationError(f"duration_constraint.source 不合法：{source}")
    if not isinstance(reason, str) or not reason.strip():
        raise ResolverValidationError("duration_constraint.reason 必须是非空字符串")
    if min_seconds is not None and max_seconds is not None and min_seconds > max_seconds:
        raise ResolverValidationError("duration_constraint 必须满足 min_seconds <= max_seconds")

    if status == "resolved" and min_seconds is None and max_seconds is None:
        raise ResolverValidationError("duration_constraint.status=resolved 时至少需要一个时长边界")
    if status == "not_specified":
        if min_seconds is not None or max_seconds is not None or source != "none":
            raise ResolverValidationError("duration_constraint.status=not_specified 时边界必须为 null 且 source=none")
    if status == "unresolved" and (min_seconds is not None or max_seconds is not None):
        raise ResolverValidationError("duration_constraint.status=unresolved 时边界必须为 null")

    return {
        "status": status,
        "min_seconds": min_seconds,
        "max_seconds": max_seconds,
        "source": source,
        "reason": reason.strip(),
    }


def validate_resolver_result(
    result: dict[str, Any],
    gt_annotation: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ResolverValidationError("Resolver 输出根节点必须是 dict")
    missing = sorted(REQUIRED_FIELDS - set(result.keys()))
    if missing:
        raise ResolverValidationError(f"Resolver 输出缺少字段：{', '.join(missing)}")

    instruction_mode = result["instruction_mode"]
    selection_scope = result["selection_scope"]
    resolution_status = result["resolution_status"]
    use_default_highlights = result["use_default_highlights"]
    relevant_segment_ids = _string_list(result["relevant_segment_ids"], "relevant_segment_ids")
    forbidden_segment_ids = _string_list(result["forbidden_segment_ids"], "forbidden_segment_ids")
    unresolved_requirements = _string_list(result["unresolved_requirements"], "unresolved_requirements")
    duration_constraint = _validate_duration_constraint(result.get("duration_constraint"))
    resolver_reason = result["resolver_reason"]

    if instruction_mode not in INSTRUCTION_MODES:
        raise ResolverValidationError(f"instruction_mode 不合法：{instruction_mode}")
    if selection_scope not in SELECTION_SCOPES:
        raise ResolverValidationError(f"selection_scope 不合法：{selection_scope}")
    if resolution_status not in RESOLUTION_STATUSES:
        raise ResolverValidationError(f"resolution_status 不合法：{resolution_status}")
    if not isinstance(use_default_highlights, bool):
        raise ResolverValidationError("use_default_highlights 必须是 bool")
    if not isinstance(resolver_reason, str) or not resolver_reason.strip():
        raise ResolverValidationError("resolver_reason 必须是非空字符串")

    _reject_duplicates(relevant_segment_ids, "relevant_segment_ids")
    _reject_duplicates(forbidden_segment_ids, "forbidden_segment_ids")
    overlap = sorted(set(relevant_segment_ids) & set(forbidden_segment_ids))
    if overlap:
        raise ResolverValidationError(f"同一 segment_id 不能同时 relevant 和 forbidden：{', '.join(overlap)}")

    existing_ids = {str(segment.get("segment_id")) for segment in gt_annotation.get("semantic_segments", [])}
    unknown_ids = sorted((set(relevant_segment_ids) | set(forbidden_segment_ids)) - existing_ids)
    if unknown_ids:
        raise ResolverValidationError(f"Resolver 引用了 GT 中不存在的 segment_id：{', '.join(unknown_ids)}")

    if instruction_mode == "generic":
        if selection_scope != "not_applicable":
            raise ResolverValidationError("generic 的 selection_scope 必须是 not_applicable")
        if resolution_status != "resolved" or not use_default_highlights:
            raise ResolverValidationError("generic 必须 resolved 且 use_default_highlights=true")
        if relevant_segment_ids or forbidden_segment_ids:
            raise ResolverValidationError("generic 的 relevant_segment_ids 和 forbidden_segment_ids 必须为空")
    elif instruction_mode == "specific":
        if selection_scope not in {"preferential", "exclusive"}:
            raise ResolverValidationError("specific 的 selection_scope 必须是 preferential 或 exclusive")
        if use_default_highlights:
            raise ResolverValidationError("specific 必须 use_default_highlights=false")
        if resolution_status == "resolved" and not relevant_segment_ids:
            raise ResolverValidationError("specific resolved 时 relevant_segment_ids 不能为空")
    elif instruction_mode == "conflict":
        if selection_scope not in {"preferential", "exclusive"}:
            raise ResolverValidationError("conflict 的 selection_scope 必须是 preferential 或 exclusive")
        if use_default_highlights:
            raise ResolverValidationError("conflict 必须 use_default_highlights=false")
        if resolution_status == "resolved" and not relevant_segment_ids and not forbidden_segment_ids:
            raise ResolverValidationError("conflict resolved 时 relevant 或 forbidden 至少一个非空")
    elif instruction_mode == "unresolved":
        if selection_scope != "unknown":
            raise ResolverValidationError("unresolved 的 selection_scope 必须是 unknown")
        if use_default_highlights:
            raise ResolverValidationError("unresolved 必须 use_default_highlights=false")
        if resolution_status not in {"unresolved", "partial"}:
            raise ResolverValidationError("unresolved 的 resolution_status 必须是 unresolved 或 partial")
        if not unresolved_requirements:
            raise ResolverValidationError("unresolved_requirements 不能为空")

    return {
        "instruction_mode": instruction_mode,
        "selection_scope": selection_scope,
        "resolution_status": resolution_status,
        "use_default_highlights": use_default_highlights,
        "relevant_segment_ids": relevant_segment_ids,
        "forbidden_segment_ids": forbidden_segment_ids,
        "duration_constraint": duration_constraint,
        "unresolved_requirements": unresolved_requirements,
        "resolver_reason": resolver_reason.strip(),
    }


def resolve_instruction_with_ark(
    *,
    instruction: str,
    target_duration: float | None,
    gt_annotation: dict[str, Any],
    config: ArkResolverConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result, metadata = call_ark_resolver(
        instruction=instruction,
        target_duration=target_duration,
        gt_annotation=gt_annotation,
        config=config,
    )
    return validate_resolver_result(result, gt_annotation), metadata
