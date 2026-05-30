from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from .ark_resolver_client import ArkResolverConfig, ArkResolverError
    from .gt_loader import load_gt_by_input_video
    from .instruction_resolver import ResolverValidationError, resolve_instruction_with_ark
    from .metrics import compute_default_highlight_metrics, compute_segment_reference_metrics
    from .resolver_prompts import RESOLVER_PROMPT_VERSION, build_resolver_user_payload
except ImportError:  # pragma: no cover - script mode
    from ark_resolver_client import ArkResolverConfig, ArkResolverError
    from gt_loader import load_gt_by_input_video
    from instruction_resolver import ResolverValidationError, resolve_instruction_with_ark
    from metrics import compute_default_highlight_metrics, compute_segment_reference_metrics
    from resolver_prompts import RESOLVER_PROMPT_VERSION, build_resolver_user_payload


@dataclass
class AutoEvalConfig:
    input_video: Path
    instruction: str
    target_duration: float | None
    skill_output_dir: Path
    gt_dir: Path
    output_dir: Path
    resolver_config: ArkResolverConfig


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_skill_segments_json(
    *,
    skill_output_dir: Path,
    input_video: Path,
) -> Path:
    direct = skill_output_dir / "reports" / "segments.json"
    if direct.exists():
        return direct
    nested = skill_output_dir / Path(input_video).stem / "reports" / "segments.json"
    if nested.exists():
        return nested
    raise FileNotFoundError(
        f"找不到 Skill segments.json：skill_output_dir={skill_output_dir} "
        f"input_video={input_video} expected={direct} 或 {nested}"
    )


def load_final_segments(segments_json_path: Path) -> list[dict[str, Any]]:
    if not segments_json_path.exists():
        raise FileNotFoundError(f"segments.json 不存在：{segments_json_path}")
    payload = json.loads(segments_json_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"segments.json 根节点必须是 object：{segments_json_path}")
    final_segments = payload.get("final_segments")
    if not isinstance(final_segments, list):
        raise ValueError(f"segments.json 缺少 final_segments 数组：{segments_json_path}")
    for index, segment in enumerate(final_segments):
        if not isinstance(segment, dict):
            raise ValueError(f"final_segments[{index}] 必须是 object")
        if "start" not in segment or "end" not in segment:
            raise ValueError(f"final_segments[{index}] 缺少 start/end")
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"final_segments[{index}] 的 start/end 必须是数字") from exc
        if start >= end:
            raise ValueError(f"final_segments[{index}] 必须满足 start < end")
    return final_segments


def _case_id(video_id: str) -> str:
    return f"auto_{video_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _generated_case(
    *,
    gt_annotation: dict[str, Any],
    instruction: str,
    target_duration: float | None,
    resolver_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "case_id": _case_id(str(gt_annotation["video_id"])),
        "video_id": gt_annotation["video_id"],
        "instruction": instruction,
        "target_duration": target_duration,
        **resolver_result,
        "resolver_backend": "ark",
        "resolver_prompt_version": RESOLVER_PROMPT_VERSION,
    }


def _score_auto_case(
    *,
    resolver_result: dict[str, Any],
    final_segments: list[dict[str, Any]],
    gt_annotation: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    if resolver_result["use_default_highlights"]:
        return "scored", compute_default_highlight_metrics(
            final_segments,
            gt_annotation.get("semantic_segments", []),
        )

    if resolver_result["resolution_status"] == "unresolved":
        return "manual_review_required", None

    metrics = compute_segment_reference_metrics(
        final_segments,
        gt_annotation.get("semantic_segments", []),
        resolver_result.get("relevant_segment_ids", []),
        resolver_result.get("forbidden_segment_ids", []),
    )
    if resolver_result["resolution_status"] == "partial":
        return "manual_review_required", metrics
    return "scored", metrics


def _write_report(path: Path, result: dict[str, Any]) -> None:
    semantic_metrics = result.get("semantic_metrics") or {}
    generated_case = result.get("generated_case") or {}
    lines = [
        "# Ark Instruction Resolver 自动评测报告",
        "",
        "## 基本信息",
        f"- evaluation_status: `{result.get('evaluation_status')}`",
        f"- video_id: `{result.get('video_id')}`",
        f"- instruction_mode: `{result.get('instruction_mode')}`",
        f"- resolution_status: `{result.get('resolution_status')}`",
        f"- resolver_backend: `{result.get('resolver_backend')}`",
        f"- final_score: `{result.get('final_score')}`",
        "",
        "## Resolver 结果",
        f"- use_default_highlights: `{generated_case.get('use_default_highlights')}`",
        f"- relevant_segment_ids: `{generated_case.get('relevant_segment_ids')}`",
        f"- forbidden_segment_ids: `{generated_case.get('forbidden_segment_ids')}`",
        f"- unresolved_requirements: `{generated_case.get('unresolved_requirements')}`",
        f"- resolver_reason: {generated_case.get('resolver_reason', '')}",
        "",
        "## 语义指标",
    ]
    if semantic_metrics:
        for key, value in semantic_metrics.items():
            lines.append(f"- {key}: `{value}`")
    else:
        lines.append("- 无自动语义指标，需要人工复核。")
    if result.get("error_message"):
        lines.extend(["", "## 错误信息", f"- {result.get('error_type')}: {result.get('error_message')}"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _failure_result(
    *,
    config: AutoEvalConfig,
    gt_annotation: dict[str, Any] | None,
    error: Exception,
) -> dict[str, Any]:
    return {
        "evaluation_status": "resolver_failed",
        "video_id": gt_annotation.get("video_id") if gt_annotation else Path(config.input_video).stem,
        "instruction": config.instruction,
        "target_duration": config.target_duration,
        "instruction_mode": None,
        "resolution_status": None,
        "resolver_backend": "ark",
        "semantic_metrics": None,
        "resolver_metadata": None,
        "final_score": None,
        "error_type": error.__class__.__name__,
        "error_message": str(error),
    }


def run_auto_eval(config: AutoEvalConfig) -> dict[str, Any]:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    gt_annotation: dict[str, Any] | None = None
    resolver_request: dict[str, Any] | None = None
    try:
        gt_annotation = load_gt_by_input_video(config.input_video, config.gt_dir)
        resolver_request = build_resolver_user_payload(
            instruction=config.instruction,
            target_duration=config.target_duration,
            gt_annotation=gt_annotation,
        )
        _write_json(config.output_dir / "resolver_request.json", resolver_request)

        resolver_result, resolver_metadata = resolve_instruction_with_ark(
            instruction=config.instruction,
            target_duration=config.target_duration,
            gt_annotation=gt_annotation,
            config=config.resolver_config,
        )
        _write_json(config.output_dir / "resolver_response.json", resolver_result)
        _write_json(config.output_dir / "resolver_metadata.json", resolver_metadata)

        generated_case = _generated_case(
            gt_annotation=gt_annotation,
            instruction=config.instruction,
            target_duration=config.target_duration,
            resolver_result=resolver_result,
        )
        _write_json(config.output_dir / "generated_case.json", generated_case)

        segments_json = find_skill_segments_json(
            skill_output_dir=config.skill_output_dir,
            input_video=config.input_video,
        )
        final_segments = load_final_segments(segments_json)
        evaluation_status, semantic_metrics = _score_auto_case(
            resolver_result=resolver_result,
            final_segments=final_segments,
            gt_annotation=gt_annotation,
        )
        result = {
            "evaluation_status": evaluation_status,
            "video_id": gt_annotation["video_id"],
            "instruction": config.instruction,
            "target_duration": config.target_duration,
            "instruction_mode": resolver_result["instruction_mode"],
            "resolution_status": resolver_result["resolution_status"],
            "resolver_backend": "ark",
            "semantic_metrics": semantic_metrics,
            "resolver_metadata": resolver_metadata,
            "generated_case": generated_case,
            "segments_json": str(segments_json),
            "final_score": None,
        }
    except (ArkResolverError, ResolverValidationError, json.JSONDecodeError) as exc:
        result = _failure_result(config=config, gt_annotation=gt_annotation, error=exc)
        if resolver_request is None and gt_annotation is not None:
            resolver_request = build_resolver_user_payload(
                instruction=config.instruction,
                target_duration=config.target_duration,
                gt_annotation=gt_annotation,
            )
            _write_json(config.output_dir / "resolver_request.json", resolver_request)

    _write_json(config.output_dir / "evaluation_result.json", result)
    _write_report(config.output_dir / "eval_report.md", result)
    return result
