from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from .aesthetic_judge import run_aesthetic_judge, sanitize_url
    from .aesthetic_judge_prompts import AESTHETIC_JUDGE_PROMPT_VERSION, build_safe_aesthetic_judge_request_record
    from .ark_aesthetic_judge_client import ArkAestheticJudgeConfig, ArkAestheticJudgeError
    from .ark_resolver_client import ArkResolverConfig, ArkResolverError
    from .artifact_validation import validate_skill_artifacts
    from .dover_quality import DoverConfig, evaluate_dover_quality
    from .gt_loader import load_gt_by_input_video
    from .instruction_resolver import ResolverValidationError, resolve_instruction_with_ark, validate_resolver_result
    from .metrics import compute_default_highlight_metrics, compute_segment_reference_metrics
    from .resolver_prompts import RESOLVER_PROMPT_VERSION, build_resolver_user_payload
    from .run_manifest import build_run_manifest, write_run_manifest
    from .selection_scoring import compute_generic_selection_score, compute_guided_selection_score
    from .technical_quality import check_technical_quality
except ImportError:  # pragma: no cover - script mode
    from aesthetic_judge import run_aesthetic_judge, sanitize_url
    from aesthetic_judge_prompts import AESTHETIC_JUDGE_PROMPT_VERSION, build_safe_aesthetic_judge_request_record
    from ark_aesthetic_judge_client import ArkAestheticJudgeConfig, ArkAestheticJudgeError
    from ark_resolver_client import ArkResolverConfig, ArkResolverError
    from artifact_validation import validate_skill_artifacts
    from dover_quality import DoverConfig, evaluate_dover_quality
    from gt_loader import load_gt_by_input_video
    from instruction_resolver import ResolverValidationError, resolve_instruction_with_ark, validate_resolver_result
    from metrics import compute_default_highlight_metrics, compute_segment_reference_metrics
    from resolver_prompts import RESOLVER_PROMPT_VERSION, build_resolver_user_payload
    from run_manifest import build_run_manifest, write_run_manifest
    from selection_scoring import compute_generic_selection_score, compute_guided_selection_score
    from technical_quality import check_technical_quality


@dataclass
class AutoEvalConfig:
    input_video: Path
    instruction: str
    target_duration: float | None
    skill_output_dir: Path
    gt_dir: Path
    output_dir: Path
    resolver_config: ArkResolverConfig
    generated_case_json: Path | None = None
    judge_video_url: str | None = None
    aesthetic_judge_config: ArkAestheticJudgeConfig | None = None
    judge_repeats: int = 1
    dover_config: DoverConfig | None = None
    technical_quality_config: Path | None = None


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


def find_skill_result_summary_json(
    *,
    skill_output_dir: Path,
    input_video: Path,
) -> Path:
    direct = skill_output_dir / "reports" / "result_summary.json"
    if direct.exists():
        return direct
    nested = skill_output_dir / Path(input_video).stem / "reports" / "result_summary.json"
    if nested.exists():
        return nested
    raise FileNotFoundError(
        f"找不到 Skill result_summary.json：skill_output_dir={skill_output_dir} "
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


def load_result_summary(result_summary_path: Path) -> dict[str, Any]:
    if not result_summary_path.exists():
        raise FileNotFoundError(f"result_summary.json 不存在：{result_summary_path}")
    payload = json.loads(result_summary_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"result_summary.json 根节点必须是 object：{result_summary_path}")
    return payload


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


def _resolver_fields_from_generated_case(generated_case: dict[str, Any]) -> dict[str, Any]:
    return {
        "instruction_mode": generated_case.get("instruction_mode"),
        "selection_scope": generated_case.get("selection_scope"),
        "resolution_status": generated_case.get("resolution_status"),
        "use_default_highlights": generated_case.get("use_default_highlights"),
        "relevant_segment_ids": generated_case.get("relevant_segment_ids"),
        "forbidden_segment_ids": generated_case.get("forbidden_segment_ids"),
        "unresolved_requirements": generated_case.get("unresolved_requirements"),
        "resolver_reason": generated_case.get("resolver_reason"),
    }


def _load_frozen_generated_case(
    *,
    generated_case_json: Path,
    gt_annotation: dict[str, Any],
    instruction: str,
    target_duration: float | None,
) -> dict[str, Any]:
    generated_case = json.loads(Path(generated_case_json).read_text(encoding="utf-8"))
    if not isinstance(generated_case, dict):
        raise ValueError(f"frozen generated_case 根节点必须是 object：{generated_case_json}")
    if generated_case.get("video_id") != gt_annotation.get("video_id"):
        raise ValueError("frozen generated_case.video_id 与当前 GT 不一致")
    if generated_case.get("instruction") != instruction:
        raise ValueError("frozen generated_case.instruction 与当前 instruction 不一致")
    frozen_target = generated_case.get("target_duration")
    if frozen_target != target_duration:
        raise ValueError("frozen generated_case.target_duration 与当前 target_duration 不一致")
    resolver_result = _resolver_fields_from_generated_case(generated_case)
    validated = validate_resolver_result(resolver_result, gt_annotation)
    return {
        **generated_case,
        **validated,
        "resolver_backend": generated_case.get("resolver_backend", "frozen"),
        "resolver_prompt_version": generated_case.get("resolver_prompt_version", RESOLVER_PROMPT_VERSION),
    }


def _legacy_metrics(
    *,
    resolver_result: dict[str, Any],
    final_segments: list[dict[str, Any]],
    gt_annotation: dict[str, Any],
) -> dict[str, Any] | None:
    if resolver_result["use_default_highlights"]:
        return compute_default_highlight_metrics(
            final_segments,
            gt_annotation.get("semantic_segments", []),
        )

    if resolver_result["resolution_status"] == "unresolved":
        return None

    return compute_segment_reference_metrics(
        final_segments,
        gt_annotation.get("semantic_segments", []),
        resolver_result.get("relevant_segment_ids", []),
        resolver_result.get("forbidden_segment_ids", []),
    )


def _sum_segment_duration(final_segments: list[dict[str, Any]]) -> float:
    return round(sum(max(0.0, float(segment["end"]) - float(segment["start"])) for segment in final_segments), 3)


def _duration_context(
    *,
    result_summary: dict[str, Any],
    gt_annotation: dict[str, Any],
    final_segments: list[dict[str, Any]],
) -> dict[str, Any]:
    policy = result_summary.get("duration_policy") if isinstance(result_summary.get("duration_policy"), dict) else {}
    mode = str(policy.get("duration_policy_mode", "bounded_auto") or "bounded_auto")
    user_specified = bool(policy.get("user_specified_duration"))
    user_target = policy.get("user_target_duration")
    selected_target = result_summary.get("selected_target_duration", policy.get("selected_target_duration"))
    final_total = result_summary.get("final_total_duration", policy.get("final_total_duration"))
    if final_total is None:
        final_total = _sum_segment_duration(final_segments)
    video_duration = float(gt_annotation.get("duration_seconds", 0))
    if user_specified:
        duration_budget = user_target
    elif mode == "bounded_auto":
        duration_budget = selected_target
    else:
        duration_budget = None
    duration_delta = None
    duration_error_ratio = None
    duration_score = None
    if duration_budget is not None and float(duration_budget) > 0:
        duration_delta = abs(float(final_total) - float(duration_budget))
        duration_error_ratio = duration_delta / float(duration_budget)
        duration_score = max(0.0, 1.0 - duration_error_ratio)
    return {
        "duration_policy_mode": mode,
        "video_duration": round(video_duration, 3),
        "user_target_duration": user_target,
        "recommended_duration": policy.get("recommended_duration"),
        "selected_target_duration": selected_target,
        "final_total_duration": round(float(final_total), 3),
        "duration_budget": duration_budget,
        "compression_ratio": round(float(final_total) / video_duration, 3) if video_duration > 0 else None,
        "duration_delta": round(duration_delta, 3) if duration_delta is not None else None,
        "duration_error_ratio": round(duration_error_ratio, 3) if duration_error_ratio is not None else None,
        "duration_score": round(duration_score, 3) if duration_score is not None else None,
    }


def _selection_score(
    *,
    resolver_result: dict[str, Any],
    final_segments: list[dict[str, Any]],
    gt_annotation: dict[str, Any],
    duration_context: dict[str, Any],
) -> tuple[str, str, dict[str, Any] | None, dict[str, Any]]:
    if resolver_result["resolution_status"] != "resolved":
        return "manual_review_required", "manual_review", None, {}
    if duration_context.get("duration_policy_mode") == "llm_free" and duration_context.get("user_target_duration") is None:
        return "diagnostic_only", "diagnostic_only", None, {}
    duration_budget = duration_context.get("duration_budget")
    duration_score = duration_context.get("duration_score")
    if duration_budget is None or float(duration_budget) <= 0 or duration_score is None:
        return "manual_review_required", "manual_review", None, {}
    if resolver_result["use_default_highlights"]:
        time_metrics = compute_generic_selection_score(
            final_segments,
            gt_annotation.get("semantic_segments", []),
            duration_budget=float(duration_budget),
            duration_score=float(duration_score),
        )
    else:
        time_metrics = compute_guided_selection_score(
            final_segments,
            gt_annotation.get("semantic_segments", []),
            relevant_segment_ids=resolver_result.get("relevant_segment_ids", []),
            forbidden_segment_ids=resolver_result.get("forbidden_segment_ids", []),
            selection_scope=resolver_result.get("selection_scope", "preferential"),
            duration_budget=float(duration_budget),
            duration_score=float(duration_score),
        )
    return "scored", "official", time_metrics.get("selection_score_v1"), time_metrics


def _write_report(path: Path, result: dict[str, Any]) -> None:
    legacy_metrics = result.get("legacy_metrics") or result.get("semantic_metrics") or {}
    time_metrics = result.get("time_metrics") or {}
    duration_context = result.get("duration_context") or {}
    generated_case = result.get("generated_case") or {}
    artifact_validation = result.get("artifact_validation") or {}
    technical_quality = result.get("technical_quality") or {}
    perceptual_quality = result.get("perceptual_video_quality") or {}
    editing_experience = result.get("editing_experience") or {}
    aesthetic_judge = result.get("aesthetic_judge") or {}
    lines = [
        "# ClawCut 完整自动评测报告",
        "",
        "## 基本信息",
        f"- evaluation_status: `{result.get('evaluation_status')}`",
        f"- evaluation_scope: `{result.get('evaluation_scope')}`",
        f"- score_version: `{result.get('score_version')}`",
        f"- selection_score_v1: `{result.get('selection_score_v1')}`",
        f"- aesthetic_score_v1: `{result.get('aesthetic_score_v1')}`",
        f"- editing_experience_score_v1: `{result.get('editing_experience_score_v1')}`",
        f"- final_score_v2: `{result.get('final_score_v2')}`",
        f"- video_id: `{result.get('video_id')}`",
        f"- instruction_mode: `{result.get('instruction_mode')}`",
        f"- selection_scope: `{result.get('selection_scope')}`",
        f"- resolution_status: `{result.get('resolution_status')}`",
        f"- resolver_backend: `{result.get('resolver_backend')}`",
        f"- final_score: `{result.get('final_score')}`",
        "",
        "## Artifact Validation",
        f"- artifact_validation_passed: `{artifact_validation.get('artifact_validation_passed')}`",
        f"- skill_backend_requested: `{artifact_validation.get('skill_backend_requested')}`",
        f"- skill_backend_used: `{artifact_validation.get('skill_backend_used')}`",
        f"- fallback_used: `{artifact_validation.get('fallback_used')}`",
        f"- errors: `{artifact_validation.get('artifact_validation_errors')}`",
        f"- warnings: `{artifact_validation.get('artifact_validation_warnings')}`",
        "",
        "## Technical Quality",
        f"- technical_quality_passed: `{technical_quality.get('technical_quality_passed')}`",
        f"- rendered_duration: `{technical_quality.get('rendered_duration')}`",
        f"- rendered_duration_error_ratio: `{technical_quality.get('rendered_duration_error_ratio')}`",
        f"- black_frame_ratio: `{technical_quality.get('black_frame_ratio')}`",
        f"- duplicate_source_ratio: `{technical_quality.get('duplicate_source_ratio')}`",
        f"- errors: `{technical_quality.get('technical_quality_errors')}`",
        f"- warnings: `{technical_quality.get('technical_quality_warnings')}`",
        "",
        "## DOVER 感知视频质量",
        f"- status: `{perceptual_quality.get('status')}`",
        f"- dover_fused_overall_score: `{perceptual_quality.get('dover_fused_overall_score')}`",
        f"- dover_raw_technical_score: `{perceptual_quality.get('dover_raw_technical_score')}`",
        f"- dover_raw_visual_aesthetic_score: `{perceptual_quality.get('dover_raw_visual_aesthetic_score')}`",
        "",
        "## Resolver 结果",
        f"- use_default_highlights: `{generated_case.get('use_default_highlights')}`",
        f"- relevant_segment_ids: `{generated_case.get('relevant_segment_ids')}`",
        f"- forbidden_segment_ids: `{generated_case.get('forbidden_segment_ids')}`",
        f"- unresolved_requirements: `{generated_case.get('unresolved_requirements')}`",
        f"- resolver_reason: {generated_case.get('resolver_reason', '')}",
        "",
        "## 时长控制",
    ]
    for key, value in duration_context.items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(
        [
            "",
            "## 时间区间级指标",
        ]
    )
    if time_metrics:
        for key, value in time_metrics.items():
            lines.append(f"- {key}: `{value}`")
    else:
        lines.append("- 无正式时间区间级分数。")
    lines.extend(
        [
            "",
            "## 旧版片段级解释指标",
        ]
    )
    if legacy_metrics:
        for key, value in legacy_metrics.items():
            lines.append(f"- {key}: `{value}`")
    else:
        lines.append("- 无旧版语义指标，需要人工复核。")
    lines.extend(["", "## Ark 剪辑体验 Judge"])
    if editing_experience:
        for key, value in editing_experience.items():
            if key in {"judge_results", "judge_metadata"}:
                continue
            lines.append(f"- {key}: `{value}`")
    else:
        lines.append("- 未执行。")
    lines.extend(
        [
            "",
            "## 最终综合分",
            "- final_score_v2 = 0.70 × selection_score_v1 + 0.30 × aesthetic_score_v1（兼容别名，等同 editing_experience_score_v1）",
            f"- final_score_v2: `{result.get('final_score_v2')}`",
        ]
    )
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
        "selection_scope": None,
        "resolution_status": None,
        "evaluation_scope": "failed",
        "score_version": "final_score_v2",
        "selection_score_v1": None,
        "aesthetic_score_v1": None,
        "final_score_v2": None,
        "resolver_backend": "ark",
        "semantic_metrics": None,
        "legacy_metrics": None,
        "time_metrics": None,
        "duration_context": None,
        "score_components": {},
        "resolver_metadata": None,
        "final_score": None,
        "error_type": error.__class__.__name__,
        "error_message": str(error),
    }


def _aesthetic_request_record(
    config: AutoEvalConfig,
    gt_annotation: dict[str, Any],
    duration_context: dict[str, Any],
    technical_quality: dict[str, Any],
) -> dict[str, Any]:
    judge_config = config.aesthetic_judge_config or ArkAestheticJudgeConfig()
    if not config.judge_video_url:
        return {"status": "pending", "reason": "未提供 --judge_video_url"}
    return build_safe_aesthetic_judge_request_record(
        judge_video_url_sanitized=sanitize_url(str(config.judge_video_url)),
        instruction=config.instruction,
        video_type=str(gt_annotation.get("video_type", "")),
        target_duration=config.target_duration,
        rendered_duration=technical_quality.get("rendered_duration") or duration_context.get("final_total_duration"),
        model=judge_config.model,
    )


def _write_manifest(
    config: AutoEvalConfig,
    gt_annotation: dict[str, Any] | None,
    artifact_validation: dict[str, Any] | None,
    generated_case: dict[str, Any] | None,
    segments_json: str | None,
) -> None:
    artifact_validation = artifact_validation or {}
    paths = artifact_validation.get("paths", {}) if isinstance(artifact_validation.get("paths"), dict) else {}
    gt_path = config.gt_dir / f"{Path(config.input_video).stem}.json"
    generated_case_path = config.output_dir / "generated_case.json"
    highlight_video_path = Path(paths["highlight_video"]) if paths.get("highlight_video") else None
    segments_json_path = Path(segments_json) if segments_json else (Path(paths["segments_json"]) if paths.get("segments_json") else None)
    duration_context = artifact_validation.get("result_summary", {}).get("duration_policy", {}) if isinstance(artifact_validation.get("result_summary"), dict) else {}
    judge_config = config.aesthetic_judge_config or ArkAestheticJudgeConfig()
    manifest = build_run_manifest(
        run_id=config.output_dir.name,
        repo_root=Path(__file__).resolve().parents[1],
        input_video_path=config.input_video,
        gt_path=gt_path if gt_path.exists() else None,
        instruction=config.instruction,
        target_duration=config.target_duration,
        duration_policy_mode=duration_context.get("duration_policy_mode"),
        skill_prompt_version=artifact_validation.get("skill_prompt_version"),
        resolver_prompt_version=(generated_case or {}).get("resolver_prompt_version") or RESOLVER_PROMPT_VERSION,
        aesthetic_judge_prompt_version=AESTHETIC_JUDGE_PROMPT_VERSION,
        skill_model=artifact_validation.get("skill_model"),
        resolver_model=config.resolver_config.model,
        aesthetic_judge_model=judge_config.model,
        skill_backend_requested=artifact_validation.get("skill_backend_requested"),
        skill_backend_used=artifact_validation.get("skill_backend_used"),
        fallback_used=artifact_validation.get("fallback_used"),
        generated_case_path=generated_case_path if generated_case_path.exists() else None,
        segments_json_path=segments_json_path,
        highlight_video_path=highlight_video_path,
        judge_repeats=config.judge_repeats,
        judge_video_url=config.judge_video_url,
    )
    write_run_manifest(config.output_dir / "run_manifest.json", manifest)


def run_auto_eval(config: AutoEvalConfig) -> dict[str, Any]:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    gt_annotation: dict[str, Any] | None = None
    resolver_request: dict[str, Any] | None = None
    artifact_validation: dict[str, Any] = {}
    technical_quality: dict[str, Any] = {}
    perceptual_video_quality: dict[str, Any] = {"provider": "DOVER", "status": "disabled", "dover_status": "disabled"}
    aesthetic_summary: dict[str, Any] | None = None
    generated_case: dict[str, Any] | None = None
    resolver_metadata: dict[str, Any] | None = None
    result: dict[str, Any]
    try:
        gt_annotation = load_gt_by_input_video(config.input_video, config.gt_dir)
        artifact_validation = validate_skill_artifacts(
            input_video=config.input_video,
            instruction=config.instruction,
            target_duration=config.target_duration,
            skill_output_dir=config.skill_output_dir,
        )
        _write_json(config.output_dir / "artifact_validation.json", artifact_validation)

        if not artifact_validation.get("artifact_validation_passed"):
            result = {
                "evaluation_status": "invalid_artifact",
                "evaluation_scope": "failed",
                "score_version": "final_score_v2",
                "selection_score_v1": None,
                "aesthetic_score_v1": None,
                "final_score_v2": None,
                "final_score": None,
                "video_id": gt_annotation.get("video_id"),
                "instruction": config.instruction,
                "target_duration": config.target_duration,
                "artifact_validation": artifact_validation,
                "technical_quality": {},
                "resolver_metadata": None,
                "generated_case": None,
                "error_type": "ArtifactValidationError",
                "error_message": "; ".join(artifact_validation.get("artifact_validation_errors", [])),
            }
            _write_json(config.output_dir / "resolver_request.json", {"status": "skipped", "reason": "invalid_artifact"})
            _write_json(config.output_dir / "resolver_response.json", {"status": "skipped", "reason": "invalid_artifact"})
            _write_json(config.output_dir / "resolver_metadata.json", {"status": "skipped", "reason": "invalid_artifact"})
            _write_json(config.output_dir / "generated_case.json", {"status": "skipped", "reason": "invalid_artifact"})
            _write_json(config.output_dir / "technical_quality.json", {"status": "skipped", "reason": "invalid_artifact"})
            _write_json(config.output_dir / "dover_quality.json", {"status": "skipped", "reason": "invalid_artifact"})
            _write_json(config.output_dir / "aesthetic_judge_request.json", {"status": "skipped", "reason": "invalid_artifact"})
            _write_json(config.output_dir / "aesthetic_judge_response.json", {"status": "skipped", "reason": "invalid_artifact"})
            _write_json(config.output_dir / "aesthetic_judge_metadata.json", {"status": "skipped", "reason": "invalid_artifact"})
            _write_json(config.output_dir / "evaluation_result.json", result)
            _write_report(config.output_dir / "eval_report.md", result)
            _write_manifest(config, gt_annotation, artifact_validation, None, None)
            return result

        official_backend_allowed = (
            artifact_validation.get("skill_backend_used") == "ark"
            and not bool(artifact_validation.get("fallback_used"))
        )

        segments_json = Path(artifact_validation["paths"]["segments_json"])
        result_summary_json = Path(artifact_validation["paths"]["result_summary"])
        highlight_video = Path(artifact_validation["paths"]["highlight_video"])
        final_segments = load_final_segments(segments_json)
        result_summary = load_result_summary(result_summary_json)
        duration_context = _duration_context(
            result_summary=result_summary,
            gt_annotation=gt_annotation,
            final_segments=final_segments,
        )

        if not official_backend_allowed:
            technical_quality = {"status": "skipped", "reason": "diagnostic_only"}
            perceptual_video_quality = {"provider": "DOVER", "status": "skipped", "dover_status": "skipped", "reason": "diagnostic_only"}
            result = {
                "evaluation_status": "diagnostic_only",
                "evaluation_scope": "diagnostic_only",
                "score_version": "final_score_v2",
                "selection_score_v1": None,
                "aesthetic_score_v1": None,
                "final_score_v2": None,
                "final_score": None,
                "video_id": gt_annotation["video_id"],
                "instruction": config.instruction,
                "target_duration": config.target_duration,
                "artifact_validation": artifact_validation,
                "technical_quality": technical_quality,
                "perceptual_video_quality": perceptual_video_quality,
                "editing_experience": {"provider": "ark_judge", "status": "skipped", "reason": "diagnostic_only"},
                "duration_context": duration_context,
                "segments_json": str(segments_json),
                "result_summary_json": str(result_summary_json),
                "error_type": "MockFallbackNotOfficial",
                "error_message": "正式评分要求 skill_backend_used=ark 且 fallback_used=false。",
            }
            _write_json(config.output_dir / "resolver_request.json", {"status": "skipped", "reason": "diagnostic_only"})
            _write_json(config.output_dir / "resolver_response.json", {"status": "skipped", "reason": "diagnostic_only"})
            _write_json(config.output_dir / "resolver_metadata.json", {"status": "skipped", "reason": "diagnostic_only"})
            _write_json(config.output_dir / "generated_case.json", {"status": "skipped", "reason": "diagnostic_only"})
            _write_json(config.output_dir / "technical_quality.json", technical_quality)
            _write_json(config.output_dir / "dover_quality.json", perceptual_video_quality)
            _write_json(config.output_dir / "aesthetic_judge_request.json", {"status": "skipped", "reason": "diagnostic_only"})
            _write_json(config.output_dir / "aesthetic_judge_response.json", {"status": "skipped", "reason": "diagnostic_only"})
            _write_json(config.output_dir / "aesthetic_judge_metadata.json", {"status": "skipped", "reason": "diagnostic_only"})
            _write_json(config.output_dir / "evaluation_result.json", result)
            _write_report(config.output_dir / "eval_report.md", result)
            _write_manifest(config, gt_annotation, artifact_validation, None, str(segments_json))
            return result

        if config.generated_case_json:
            generated_case = _load_frozen_generated_case(
                generated_case_json=config.generated_case_json,
                gt_annotation=gt_annotation,
                instruction=config.instruction,
                target_duration=config.target_duration,
            )
            resolver_result = _resolver_fields_from_generated_case(generated_case)
            resolver_metadata = {
                "resolver_backend": "frozen",
                "source_generated_case_json": str(config.generated_case_json),
                "resolver_prompt_version": generated_case.get("resolver_prompt_version"),
            }
            _write_json(config.output_dir / "resolver_request.json", {"status": "skipped", "reason": "frozen_generated_case"})
            _write_json(config.output_dir / "resolver_response.json", _resolver_fields_from_generated_case(generated_case))
        else:
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
        if resolver_metadata:
            _write_json(config.output_dir / "resolver_metadata.json", resolver_metadata)
        legacy_metrics = _legacy_metrics(
            resolver_result=resolver_result,
            final_segments=final_segments,
            gt_annotation=gt_annotation,
        )
        evaluation_status, evaluation_scope, selection_score_v1, time_metrics = _selection_score(
            resolver_result=resolver_result,
            final_segments=final_segments,
            gt_annotation=gt_annotation,
            duration_context=duration_context,
        )
        technical_quality = check_technical_quality(
            input_video=config.input_video,
            highlight_video=highlight_video,
            final_segments=final_segments,
            source_video_duration=artifact_validation.get("source_video_duration") or duration_context.get("video_duration"),
            config_path=config.technical_quality_config,
        )
        _write_json(config.output_dir / "technical_quality.json", technical_quality)
        if technical_quality.get("technical_quality_passed"):
            perceptual_video_quality = evaluate_dover_quality(
                highlight_video,
                config.dover_config or DoverConfig(enabled=False),
            )
        else:
            perceptual_video_quality = {"provider": "DOVER", "status": "skipped", "dover_status": "skipped", "reason": "technical_quality_failed"}
        _write_json(config.output_dir / "dover_quality.json", perceptual_video_quality)
        technical_passed = bool(technical_quality.get("technical_quality_passed"))
        status = evaluation_status
        if perceptual_video_quality.get("dover_required_failed"):
            status = "dover_failed"
        if evaluation_status == "scored" and not technical_passed:
            status = "technical_quality_failed"
        elif status == "scored" and not config.judge_video_url:
            status = "selection_scored_aesthetic_pending"

        _write_json(config.output_dir / "aesthetic_judge_request.json", _aesthetic_request_record(config, gt_annotation, duration_context, technical_quality))
        if status == "selection_scored_aesthetic_pending":
            _write_json(config.output_dir / "aesthetic_judge_response.json", {"status": "pending", "reason": "未提供 --judge_video_url"})
            _write_json(config.output_dir / "aesthetic_judge_metadata.json", {"status": "pending"})
        elif status in {"manual_review_required", "diagnostic_only", "technical_quality_failed", "dover_failed"}:
            _write_json(config.output_dir / "aesthetic_judge_response.json", {"status": "skipped", "reason": status})
            _write_json(config.output_dir / "aesthetic_judge_metadata.json", {"status": "skipped", "reason": status})
        else:
            try:
                judge_config = config.aesthetic_judge_config or ArkAestheticJudgeConfig()
                aesthetic_summary = run_aesthetic_judge(
                    judge_video_url=str(config.judge_video_url),
                    instruction=config.instruction,
                    video_type=str(gt_annotation.get("video_type", "")),
                    target_duration=config.target_duration,
                    rendered_duration=technical_quality.get("rendered_duration"),
                    config=judge_config,
                    repeats=config.judge_repeats,
                )
                _write_json(config.output_dir / "aesthetic_judge_response.json", aesthetic_summary)
                _write_json(
                    config.output_dir / "aesthetic_judge_metadata.json",
                    {"judge_metadata": aesthetic_summary.get("judge_metadata", [])},
                )
            except Exception as exc:
                aesthetic_summary = {
                    "judge_status": "failed",
                    "editing_experience_score_v1": None,
                    "aesthetic_score_v1": None,
                    "aesthetic_score_v1_deprecated_alias": True,
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc),
                }
                status = "judge_failed"
                _write_json(config.output_dir / "aesthetic_judge_response.json", aesthetic_summary)
                _write_json(config.output_dir / "aesthetic_judge_metadata.json", {"status": "failed"})

        aesthetic_score_v1 = aesthetic_summary.get("aesthetic_score_v1") if aesthetic_summary else None
        editing_experience_score_v1 = (
            aesthetic_summary.get("editing_experience_score_v1") if aesthetic_summary else None
        )
        final_score_v2 = None
        if aesthetic_summary and aesthetic_summary.get("judge_status") == "scored":
            if aesthetic_summary.get("judge_stability_warning") or aesthetic_summary.get("judge_manual_review_required"):
                status = "judge_manual_review_required"
            elif (
                status == "scored"
                and evaluation_scope == "official"
                and technical_passed
                and selection_score_v1 is not None
                and aesthetic_score_v1 is not None
            ):
                final_score_v2 = round(0.70 * float(selection_score_v1) + 0.30 * float(aesthetic_score_v1), 3)
                status = "scored_complete"

        result = {
            "evaluation_status": status,
            "evaluation_scope": evaluation_scope,
            "score_version": "final_score_v2",
            "selection_score_v1": selection_score_v1,
            "editing_experience_score_v1": editing_experience_score_v1,
            "aesthetic_score_v1": aesthetic_score_v1,
            "final_score_v2": final_score_v2,
            "video_id": gt_annotation["video_id"],
            "instruction": config.instruction,
            "target_duration": config.target_duration,
            "instruction_mode": resolver_result["instruction_mode"],
            "selection_scope": resolver_result.get("selection_scope"),
            "resolution_status": resolver_result["resolution_status"],
            "resolver_backend": generated_case.get("resolver_backend", "ark"),
            "semantic_metrics": legacy_metrics,
            "legacy_metrics": legacy_metrics,
            "time_metrics": time_metrics,
            "duration_context": duration_context,
            "score_components": {
                "duration_score": duration_context.get("duration_score"),
                "selection_score_v1": selection_score_v1,
                "editing_experience_score_v1": editing_experience_score_v1,
                "aesthetic_score_v1": aesthetic_score_v1,
                "final_score_v2": final_score_v2,
            },
            "selection_quality": time_metrics,
            "artifact_validation": artifact_validation,
            "technical_quality": technical_quality,
            "perceptual_video_quality": perceptual_video_quality,
            "editing_experience": {
                "provider": "ark_judge",
                "status": aesthetic_summary.get("judge_status") if aesthetic_summary else ("pending" if status == "selection_scored_aesthetic_pending" else "skipped"),
                "editing_experience_score_v1": editing_experience_score_v1,
                "aesthetic_score_v1": aesthetic_score_v1,
                "aesthetic_score_v1_deprecated_alias": True,
                "judge_confidence": aesthetic_summary.get("judge_confidence") if aesthetic_summary else None,
                "manual_review_recommended": aesthetic_summary.get("judge_manual_review_required") if aesthetic_summary else None,
                "judge_summary": (aesthetic_summary.get("judge_results", [{}])[0].get("judge_summary") if aesthetic_summary and aesthetic_summary.get("judge_results") else None),
            },
            "aesthetic_judge": aesthetic_summary,
            "resolver_metadata": resolver_metadata,
            "generated_case": generated_case,
            "segments_json": str(segments_json),
            "result_summary_json": str(result_summary_json),
            "final_score": final_score_v2,
        }
    except (ArkResolverError, ResolverValidationError, json.JSONDecodeError) as exc:
        if config.generated_case_json is not None:
            raise
        result = _failure_result(config=config, gt_annotation=gt_annotation, error=exc)
        result["artifact_validation"] = artifact_validation
        result["technical_quality"] = technical_quality
        if resolver_request is None and gt_annotation is not None:
            resolver_request = build_resolver_user_payload(
                instruction=config.instruction,
                target_duration=config.target_duration,
                gt_annotation=gt_annotation,
            )
            _write_json(config.output_dir / "resolver_request.json", resolver_request)
        _write_json(config.output_dir / "resolver_response.json", {"status": "failed", "reason": "resolver_failed"})
        _write_json(config.output_dir / "resolver_metadata.json", {"status": "failed", "reason": "resolver_failed"})
        _write_json(config.output_dir / "generated_case.json", {"status": "skipped", "reason": "resolver_failed"})
        _write_json(config.output_dir / "technical_quality.json", technical_quality or {"status": "skipped", "reason": "resolver_failed"})
        _write_json(config.output_dir / "dover_quality.json", perceptual_video_quality or {"status": "skipped", "reason": "resolver_failed"})
        _write_json(config.output_dir / "aesthetic_judge_request.json", {"status": "skipped", "reason": "resolver_failed"})
        _write_json(config.output_dir / "aesthetic_judge_response.json", {"status": "skipped", "reason": "resolver_failed"})
        _write_json(config.output_dir / "aesthetic_judge_metadata.json", {"status": "skipped", "reason": "resolver_failed"})

    _write_json(config.output_dir / "evaluation_result.json", result)
    _write_report(config.output_dir / "eval_report.md", result)
    _write_manifest(
        config,
        gt_annotation,
        result.get("artifact_validation") or artifact_validation,
        generated_case,
        result.get("segments_json"),
    )
    return result
