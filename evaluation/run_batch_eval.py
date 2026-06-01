from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any

try:
    from .ark_aesthetic_judge_client import ArkAestheticJudgeConfig
    from .ark_resolver_client import ArkResolverConfig
    from .auto_eval import AutoEvalConfig, run_auto_eval
    from .dover_quality import build_dover_config
    from .tos_uploader import build_tos_upload_config
except ImportError:  # pragma: no cover - script mode
    from ark_aesthetic_judge_client import ArkAestheticJudgeConfig
    from ark_resolver_client import ArkResolverConfig
    from auto_eval import AutoEvalConfig, run_auto_eval
    from dover_quality import build_dover_config
    from tos_uploader import build_tos_upload_config


CSV_FIELDS = [
    "case_id",
    "video_id",
    "instruction",
    "instruction_mode",
    "selection_scope",
    "evaluation_status",
    "evaluation_scope",
    "artifact_validation_passed",
    "technical_quality_passed",
    "skill_backend_used",
    "fallback_used",
    "selection_score_v1",
    "aesthetic_score_v1",
    "final_score_v2",
    "compression_ratio",
    "duration_score",
    "generic_value_score",
    "relevant_duration_precision",
    "relevant_duration_coverage",
    "relevant_duration_f1",
    "forbidden_duration_ratio",
    "avoid_by_default_overlap_ratio",
    "duplicate_source_ratio",
    "planned_total_duration",
    "rendered_duration",
    "rendered_duration_error_ratio",
    "black_frame_ratio",
    "freeze_frame_ratio",
    "silence_ratio",
    "dover_status",
    "dover_fused_overall_score",
    "dover_raw_technical_score",
    "dover_raw_visual_aesthetic_score",
    "editing_experience_score_v1",
    "judge_video_upload_status",
    "decode_success",
    "audio_stream_consistent",
    "judge_confidence",
    "manual_review_recommended",
    "judge_stability_warning",
    "elapsed_seconds",
]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text:
                rows.append(json.loads(text))
    return rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _row(case: dict[str, Any], result: dict[str, Any], elapsed: float) -> dict[str, Any]:
    artifact = result.get("artifact_validation") or {}
    technical = result.get("technical_quality") or {}
    duration = result.get("duration_context") or {}
    time_metrics = result.get("time_metrics") or {}
    aesthetic = result.get("aesthetic_judge") or {}
    perceptual = result.get("perceptual_video_quality") or {}
    editing = result.get("editing_experience") or {}
    upload = result.get("judge_video_upload") or {}
    return {
        "case_id": case.get("case_id", ""),
        "video_id": result.get("video_id", ""),
        "instruction": case.get("instruction", ""),
        "instruction_mode": result.get("instruction_mode"),
        "selection_scope": result.get("selection_scope"),
        "evaluation_status": result.get("evaluation_status"),
        "evaluation_scope": result.get("evaluation_scope"),
        "artifact_validation_passed": artifact.get("artifact_validation_passed"),
        "technical_quality_passed": technical.get("technical_quality_passed"),
        "skill_backend_used": artifact.get("skill_backend_used"),
        "fallback_used": artifact.get("fallback_used"),
        "selection_score_v1": result.get("selection_score_v1"),
        "aesthetic_score_v1": result.get("aesthetic_score_v1"),
        "final_score_v2": result.get("final_score_v2"),
        "compression_ratio": duration.get("compression_ratio") or technical.get("compression_ratio"),
        "duration_score": duration.get("duration_score"),
        "generic_value_score": time_metrics.get("generic_value_score"),
        "relevant_duration_precision": time_metrics.get("relevant_duration_precision"),
        "relevant_duration_coverage": time_metrics.get("relevant_duration_coverage"),
        "relevant_duration_f1": time_metrics.get("relevant_duration_f1"),
        "forbidden_duration_ratio": time_metrics.get("forbidden_duration_ratio"),
        "avoid_by_default_overlap_ratio": time_metrics.get("avoid_by_default_overlap_ratio"),
        "duplicate_source_ratio": technical.get("duplicate_source_ratio"),
        "planned_total_duration": technical.get("planned_total_duration"),
        "rendered_duration": technical.get("rendered_duration"),
        "rendered_duration_error_ratio": technical.get("rendered_duration_error_ratio"),
        "black_frame_ratio": technical.get("black_frame_ratio"),
        "freeze_frame_ratio": technical.get("freeze_frame_ratio"),
        "silence_ratio": technical.get("silence_ratio"),
        "dover_status": perceptual.get("dover_status") or perceptual.get("status"),
        "dover_fused_overall_score": perceptual.get("dover_fused_overall_score"),
        "dover_raw_technical_score": perceptual.get("dover_raw_technical_score"),
        "dover_raw_visual_aesthetic_score": perceptual.get("dover_raw_visual_aesthetic_score"),
        "editing_experience_score_v1": result.get("editing_experience_score_v1") or editing.get("editing_experience_score_v1"),
        "judge_video_upload_status": upload.get("upload_status") or upload.get("status"),
        "decode_success": technical.get("decode_success"),
        "audio_stream_consistent": technical.get("audio_stream_consistent"),
        "judge_confidence": aesthetic.get("judge_confidence") or editing.get("judge_confidence"),
        "manual_review_recommended": editing.get("manual_review_recommended"),
        "judge_stability_warning": aesthetic.get("judge_stability_warning"),
        "elapsed_seconds": round(elapsed, 3),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in CSV_FIELDS})


def main() -> int:
    parser = argparse.ArgumentParser(description="批量运行 ClawCut 自动评测。")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--gt_dir", type=Path, required=True)
    parser.add_argument("--output_dir", type=Path, required=True)
    parser.add_argument("--resolver_model", default="ep-20260526173832-2vrr2")
    parser.add_argument("--resolver_base_url", default="https://ark.cn-beijing.volces.com/api/v3")
    parser.add_argument("--resolver_api_key_env", default="ARK_API_KEY")
    parser.add_argument("--judge_model", default="ep-20260526173832-2vrr2")
    parser.add_argument("--judge_base_url", default="https://ark.cn-beijing.volces.com/api/v3")
    parser.add_argument("--judge_api_key_env", default="ARK_API_KEY")
    parser.add_argument("--judge_repeats", type=int, default=1)
    parser.add_argument("--auto_upload_judge_video", action="store_true")
    parser.add_argument("--tos_bucket", default=None)
    parser.add_argument("--tos_region", default=None)
    parser.add_argument("--tos_endpoint", default=None)
    parser.add_argument("--tos_key_prefix", default=None)
    parser.add_argument("--tos_presign_expires_seconds", type=int)
    parser.add_argument("--enable_dover", action="store_true")
    parser.add_argument("--require_dover", action="store_true")
    parser.add_argument("--dover_repo_dir", type=Path)
    parser.add_argument("--dover_python")
    parser.add_argument("--dover_opt_path", type=Path)
    parser.add_argument("--dover_device", default=None)
    parser.add_argument("--dover_timeout_seconds", type=int)
    parser.add_argument("--technical_quality_config", type=Path, default=Path("evaluation/config/default.yaml"))
    args = parser.parse_args()

    cases = _read_jsonl(args.cases)
    runs_dir = args.output_dir / "runs"
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        case_id = str(case.get("case_id") or f"case_{index:03d}")
        run_dir = runs_dir / case_id
        started = time.time()
        try:
            result = run_auto_eval(
                AutoEvalConfig(
                    input_video=Path(case["input_video"]),
                    instruction=str(case["instruction"]),
                    target_duration=case.get("target_duration"),
                    skill_output_dir=Path(case["skill_output_dir"]),
                    gt_dir=args.gt_dir,
                    output_dir=run_dir,
                    resolver_config=ArkResolverConfig(
                        model=args.resolver_model,
                        base_url=args.resolver_base_url,
                        api_key_env=args.resolver_api_key_env,
                    ),
                    generated_case_json=Path(case["generated_case_json"]) if case.get("generated_case_json") else None,
                    judge_video_url=case.get("judge_video_url"),
                    aesthetic_judge_config=ArkAestheticJudgeConfig(
                        model=args.judge_model,
                        base_url=args.judge_base_url,
                        api_key_env=args.judge_api_key_env,
                    ),
                    judge_repeats=args.judge_repeats,
                    dover_config=build_dover_config(
                        enabled=bool(args.enable_dover),
                        require_dover=bool(args.require_dover),
                        repo_dir=args.dover_repo_dir,
                        python=args.dover_python,
                        opt_path=args.dover_opt_path,
                        device=args.dover_device,
                        timeout_seconds=args.dover_timeout_seconds,
                    ),
                    technical_quality_config=args.technical_quality_config,
                    auto_upload_judge_video=bool(args.auto_upload_judge_video),
                    tos_upload_config=build_tos_upload_config(
                        enabled=bool(args.auto_upload_judge_video),
                        bucket=case.get("tos_bucket") or args.tos_bucket,
                        region=case.get("tos_region") or args.tos_region,
                        endpoint=case.get("tos_endpoint") or args.tos_endpoint,
                        key_prefix=case.get("tos_key_prefix") or args.tos_key_prefix,
                        presign_expires_seconds=case.get("tos_presign_expires_seconds") or args.tos_presign_expires_seconds,
                    ),
                )
            )
        except Exception as exc:
            result = {
                "evaluation_status": "batch_case_failed",
                "video_id": Path(str(case.get("input_video", ""))).stem,
                "instruction": case.get("instruction", ""),
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            }
            failures.append({"case_id": case_id, "error_type": exc.__class__.__name__, "error_message": str(exc)})
            _write_json(run_dir / "evaluation_result.json", result)
        rows.append(_row(case | {"case_id": case_id}, result, time.time() - started))

    _write_csv(args.output_dir / "results.csv", rows)
    summary = {
        "case_count": len(cases),
        "failure_count": len(failures),
        "scored_complete_count": sum(1 for row in rows if row.get("evaluation_status") == "scored_complete"),
        "failures": failures,
    }
    _write_json(args.output_dir / "summary.json", summary)
    lines = [
        "# ClawCut 批量评测汇总",
        "",
        f"- case_count: {summary['case_count']}",
        f"- scored_complete_count: {summary['scored_complete_count']}",
        f"- failure_count: {summary['failure_count']}",
        "",
        "| case_id | evaluation_status | selection_score_v1 | aesthetic_score_v1 | final_score_v2 |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row.get('case_id')} | {row.get('evaluation_status')} | {row.get('selection_score_v1')} | {row.get('aesthetic_score_v1')} | {row.get('final_score_v2')} |"
        )
    (args.output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"批量评测完成：{args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
