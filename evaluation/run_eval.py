from __future__ import annotations

import argparse
import csv
import json
import shlex
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT_DIR / "skills" / "clawcut-video-highlight" / "scripts"
RUN_SKILL = SCRIPTS_DIR / "run_skill.py"
sys.path.insert(0, str(SCRIPTS_DIR))

from ark_aesthetic_judge_client import ArkAestheticJudgeConfig  # noqa: E402
from ark_resolver_client import ArkResolverConfig  # noqa: E402
from auto_eval import AutoEvalConfig, run_auto_eval  # noqa: E402
from gt_loader import load_gt_dir  # noqa: E402
from metrics import (  # noqa: E402
    compute_case_score,
    compute_default_highlight_metrics,
    compute_duration_metrics,
    compute_excluded_highlight_summary,
    compute_functional_completeness,
    compute_must_avoid_violation,
    compute_must_cover_tag_coverage,
    compute_plan_metrics,
    description_mock_judge,
    match_pred_to_semantic_segments,
    resolve_target_duration,
    segment_duration,
)
from plan_validator import validate_plan  # noqa: E402
from utils import load_config, read_json  # noqa: E402


CSV_FIELDS = [
    "case_id",
    "video_id",
    "video_type",
    "instruction_type",
    "judge_mode",
    "annotation_coverage",
    "target_duration",
    "selected_target_duration",
    "final_total_duration",
    "duration_delta",
    "run_status",
    "matched_segment_ids",
    "matched_tags",
    "must_cover_tags",
    "missed_must_cover_tags",
    "must_cover_coverage",
    "violated_tags",
    "must_avoid_violation_rate",
    "default_highlight_recall",
    "default_highlight_precision",
    "default_highlight_f1",
    "avg_default_highlight_iou",
    "selected_low_value_segments",
    "semantic_match_score",
    "matched_evidence",
    "description_judge_warning",
    "functional_completeness_score",
    "highlight_video_exists",
    "segments_json_exists",
    "report_md_exists",
    "result_summary_exists",
    "run_log_exists",
    "elapsed_seconds",
    "fallback_used",
    "excluded_highlights_count",
    "excluded_reasons",
    "evaluation_status",
    "final_score",
    "notes",
]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                records.append(json.loads(text))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} 不是合法 JSONL：{exc}") from exc
    return records


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_list(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ";".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_json_if_exists(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _find_first(pattern_root: Path, pattern: str) -> Path | None:
    matches = sorted(pattern_root.glob(pattern))
    return matches[0] if matches else None


def _find_run_outputs(case_run_root: Path) -> dict[str, Path | None]:
    result_summary = _find_first(case_run_root, "**/reports/result_summary.json")
    segments_json = _find_first(case_run_root, "**/reports/segments.json")
    report_md = _find_first(case_run_root, "**/reports/report.md")
    run_log = _find_first(case_run_root, "**/logs/run.log")
    highlight_video = _find_first(case_run_root, "**/videos/highlight.mp4")
    return {
        "result_summary_path": result_summary,
        "segments_json": segments_json,
        "report_md": report_md,
        "run_log": run_log,
        "highlight_video": highlight_video,
    }


def _build_run_skill_command(
    case: dict[str, Any],
    annotation: dict[str, Any],
    case_run_root: Path,
    backend: str,
) -> list[str]:
    command = [
        sys.executable,
        str(RUN_SKILL),
        "--input_video",
        str(annotation["video_path"]),
        "--instruction",
        str(case["instruction"]),
        "--output_dir",
        str(case_run_root),
        "--llm_backend",
        backend,
    ]
    if case.get("target_duration") is not None:
        command.extend(["--target_duration", str(case["target_duration"])])
    return command


def _detect_fallback(run_log: Path | None) -> bool:
    if not run_log or not run_log.exists():
        return False
    text = run_log.read_text(encoding="utf-8", errors="ignore")
    return "回退到 mock" in text or "fallback" in text.lower()


def _result_payload_from_outputs(outputs: dict[str, Path | None]) -> tuple[dict[str, Any], dict[str, Any]]:
    summary = _load_json_if_exists(outputs.get("result_summary_path"))
    segments = _load_json_if_exists(outputs.get("segments_json"))
    payload = dict(segments)
    payload.update(summary)
    if "final_segments" not in payload and segments.get("final_segments"):
        payload["final_segments"] = segments["final_segments"]
    if "excluded_highlights" not in payload and segments.get("excluded_highlights") is not None:
        payload["excluded_highlights"] = segments.get("excluded_highlights", [])
    return payload, {"result_summary": summary, "segments": segments}


def _compute_explainability(result_payload: dict[str, Any], outputs: dict[str, Path | None]) -> float:
    segments = result_payload.get("final_segments", [])
    if not segments:
        return 0.0
    explained = 0
    for segment in segments:
        if str(segment.get("title", "")).strip() and str(segment.get("reason", "")).strip():
            explained += 1
    report_bonus = 1.0 if outputs.get("report_md") and outputs["report_md"].exists() else 0.0
    return round(min(10.0, explained / len(segments) * 9.0 + report_bonus), 3)


def _score_case(
    case: dict[str, Any],
    annotation: dict[str, Any],
    result_payload: dict[str, Any],
    outputs: dict[str, Path | None],
) -> dict[str, Any]:
    pred_segments = result_payload.get("final_segments", [])
    semantic_segments = annotation.get("semantic_segments", [])
    excluded_highlights = result_payload.get("excluded_highlights", [])
    match = match_pred_to_semantic_segments(pred_segments, semantic_segments)
    default_highlight = compute_default_highlight_metrics(pred_segments, semantic_segments)
    must_cover = compute_must_cover_tag_coverage(
        match["matched_tags"],
        case.get("must_cover_tags", []),
    )
    must_avoid = compute_must_avoid_violation(
        match["matched_tags"],
        case.get("must_avoid_tags", []),
        match["per_pred_matches"],
    )
    description_judge = description_mock_judge(
        str(case.get("instruction", "")),
        match["matched_descriptions"],
        semantic_segments,
    )
    duration = compute_duration_metrics(result_payload, case.get("target_duration"))
    excluded = compute_excluded_highlight_summary(excluded_highlights)
    functional = compute_functional_completeness(
        {
            "highlight_video": outputs.get("highlight_video") or result_payload.get("highlight_video", ""),
            "segments_json": outputs.get("segments_json") or result_payload.get("segments_json", ""),
            "report_md": outputs.get("report_md") or result_payload.get("report_md", ""),
            "result_summary_path": outputs.get("result_summary_path") or "",
            "run_log": outputs.get("run_log") or result_payload.get("run_log", ""),
            "result_summary_status": result_payload.get("status"),
        }
    )
    metrics = {
        "match": match,
        "default_highlight": default_highlight,
        "must_cover": must_cover,
        "must_avoid": must_avoid,
        "description_judge": description_judge,
        "duration": duration,
        "excluded_highlights": excluded,
        "functional_completeness": functional,
        "explainability_score": _compute_explainability(result_payload, outputs),
    }
    score = compute_case_score(case, metrics)
    return {
        "metrics": metrics,
        "score": score,
    }


def _csv_row_from_result(result: dict[str, Any]) -> dict[str, Any]:
    case = result["case"]
    annotation = result["annotation"]
    metrics = result.get("metrics", {})
    match = metrics.get("match", {})
    must_cover = metrics.get("must_cover", {})
    must_avoid = metrics.get("must_avoid", {})
    default_highlight = metrics.get("default_highlight", {})
    description = metrics.get("description_judge", {})
    duration = metrics.get("duration", {})
    functional = metrics.get("functional_completeness", {})
    excluded = metrics.get("excluded_highlights", {})
    score = result.get("score", {})
    row = {
        "case_id": case.get("case_id", ""),
        "video_id": case.get("video_id", ""),
        "video_type": annotation.get("video_type", ""),
        "instruction_type": case.get("instruction_type", ""),
        "judge_mode": case.get("judge_mode", ""),
        "annotation_coverage": case.get("annotation_coverage", ""),
        "target_duration": case.get("target_duration"),
        "selected_target_duration": duration.get("selected_target_duration"),
        "final_total_duration": duration.get("final_total_duration"),
        "duration_delta": duration.get("duration_delta"),
        "run_status": result.get("run_status", ""),
        "matched_segment_ids": match.get("matched_segment_ids", []),
        "matched_tags": match.get("matched_tags", []),
        "must_cover_tags": case.get("must_cover_tags", []),
        "missed_must_cover_tags": must_cover.get("missed_must_cover_tags", []),
        "must_cover_coverage": must_cover.get("must_cover_coverage"),
        "violated_tags": must_avoid.get("violated_tags", []),
        "must_avoid_violation_rate": must_avoid.get("violation_rate"),
        "default_highlight_recall": default_highlight.get("default_highlight_recall"),
        "default_highlight_precision": default_highlight.get("default_highlight_precision"),
        "default_highlight_f1": default_highlight.get("default_highlight_f1"),
        "avg_default_highlight_iou": default_highlight.get("avg_default_highlight_iou"),
        "selected_low_value_segments": default_highlight.get("selected_low_value_segments", []),
        "semantic_match_score": description.get("semantic_match_score"),
        "matched_evidence": description.get("matched_evidence", []),
        "description_judge_warning": description.get("warning", ""),
        "functional_completeness_score": functional.get("functional_completeness_score"),
        "highlight_video_exists": functional.get("highlight_video_exists"),
        "segments_json_exists": functional.get("segments_json_exists"),
        "report_md_exists": functional.get("report_md_exists"),
        "result_summary_exists": functional.get("result_summary_exists"),
        "run_log_exists": functional.get("run_log_exists"),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "fallback_used": result.get("fallback_used"),
        "excluded_highlights_count": excluded.get("excluded_count"),
        "excluded_reasons": excluded.get("excluded_reasons", {}),
        "evaluation_status": score.get("evaluation_status", result.get("evaluation_status", "")),
        "final_score": score.get("final_score"),
        "notes": case.get("notes", ""),
    }
    return {field: _safe_list(row.get(field)) for field in CSV_FIELDS}


def _write_results_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _average(values: list[float]) -> float | None:
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return round(sum(valid) / len(valid), 3)


def _group_rows(results: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        grouped[str(result["case"].get(key, ""))].append(result)
    summary: dict[str, dict[str, Any]] = {}
    for group, items in grouped.items():
        scored = [item for item in items if item.get("score", {}).get("final_score") is not None]
        summary[group] = {
            "case_count": len(items),
            "scored_count": len(scored),
            "average_final_score": _average([_safe_float(item.get("score", {}).get("final_score")) for item in scored]),
        }
    return summary


def _write_eval_report(
    path: Path,
    *,
    backend: str,
    cases_path: Path,
    annotations_path: str,
    output_dir: Path,
    results: list[dict[str, Any]],
    dry_run: bool,
) -> None:
    scored = [result for result in results if result.get("score", {}).get("final_score") is not None]
    manual_only = [
        result
        for result in results
        if result.get("score", {}).get("evaluation_status") == "manual_only"
        or result["case"].get("annotation_coverage") == "uncovered"
    ]
    success_count = sum(1 for result in results if result.get("run_status") == "success")
    failed_count = sum(1 for result in results if result.get("run_status") == "failed")
    average_final_score = _average([_safe_float(result.get("score", {}).get("final_score")) for result in scored])
    average_must_cover = _average(
        [
            _safe_float(result.get("metrics", {}).get("must_cover", {}).get("must_cover_coverage"))
            for result in scored
        ]
    )
    average_must_avoid = _average(
        [
            _safe_float(result.get("metrics", {}).get("must_avoid", {}).get("violation_rate"))
            for result in scored
        ]
    )
    average_duration_delta = _average(
        [
            _safe_float(result.get("metrics", {}).get("duration", {}).get("duration_delta"))
            for result in scored
        ]
    )
    average_functional = _average(
        [
            _safe_float(result.get("metrics", {}).get("functional_completeness", {}).get("functional_completeness_score"))
            for result in scored
        ]
    )
    average_default_precision = _average(
        [
            _safe_float(result.get("metrics", {}).get("default_highlight", {}).get("default_highlight_precision"))
            for result in scored
        ]
    )
    average_default_recall = _average(
        [
            _safe_float(result.get("metrics", {}).get("default_highlight", {}).get("default_highlight_recall"))
            for result in scored
        ]
    )
    average_default_f1 = _average(
        [
            _safe_float(result.get("metrics", {}).get("default_highlight", {}).get("default_highlight_f1"))
            for result in scored
        ]
    )

    lines = [
        "# ClawCut Mock 自动评测报告",
        "",
        "## 1. 评测设置",
        f"- 模型后端：`{backend}`",
        f"- 测试用例文件：`{cases_path}`",
        f"- GT 来源：`{annotations_path}`",
        f"- 输出目录：`{output_dir}`",
        f"- 运行时间：`{datetime.now().isoformat(timespec='seconds')}`",
        f"- 是否为 dry run：`{dry_run}`",
        f"- 是否使用 mock 后端：`{backend == 'mock'}`",
        "- 是否启用真实 LLM Judge：`false`",
        "",
        "## 2. 整体结果",
        f"- 用例总数：{len(results)}",
        f"- 成功运行数：{success_count}",
        f"- 运行失败数：{failed_count}",
        f"- 自动计分用例数：{len(scored)}",
        f"- 仅人工分析用例数：{len(manual_only)}",
        f"- 平均总分：{average_final_score}",
        f"- 平均必选标签覆盖率：{average_must_cover}",
        f"- 平均禁选标签违规率：{average_must_avoid}",
        f"- 平均默认高光 Precision：{average_default_precision}",
        f"- 平均默认高光 Recall：{average_default_recall}",
        f"- 平均默认高光 F1：{average_default_f1}",
        f"- 平均时长误差：{average_duration_delta}",
        f"- 平均工程完整性分数：{average_functional}",
        "",
        "## 3. 按指令类型汇总",
    ]
    for group, summary in _group_rows(results, "instruction_type").items():
        lines.append(
            f"- {group}: 用例数={summary['case_count']}, "
            f"自动计分数={summary['scored_count']}, "
            f"平均总分={summary['average_final_score']}"
        )
    lines.extend(["", "## 4. 按评测模式汇总"])
    for group, summary in _group_rows(results, "judge_mode").items():
        lines.append(
            f"- {group}: 用例数={summary['case_count']}, "
            f"自动计分数={summary['scored_count']}, "
            f"平均总分={summary['average_final_score']}"
        )

    lines.extend(
        [
            "",
            "## 5. 用例结果表",
            "",
            "| 用例 ID | 指令类型 | 评测模式 | 运行状态 | 总分 | 必选标签覆盖率 | 禁选标签违规率 | 默认高光 Precision | 默认高光 Recall | 默认高光 F1 | 时长误差 | 评测状态 |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for result in results:
        metrics = result.get("metrics", {})
        lines.append(
            "| {case_id} | {instruction_type} | {judge_mode} | {run_status} | {final_score} | "
            "{must_cover} | {must_avoid} | {default_precision} | {default_recall} | {default_f1} | "
            "{duration_delta} | {evaluation_status} |".format(
                case_id=result["case"].get("case_id", ""),
                instruction_type=result["case"].get("instruction_type", ""),
                judge_mode=result["case"].get("judge_mode", ""),
                run_status=result.get("run_status", ""),
                final_score=result.get("score", {}).get("final_score"),
                must_cover=metrics.get("must_cover", {}).get("must_cover_coverage"),
                must_avoid=metrics.get("must_avoid", {}).get("violation_rate"),
                default_precision=metrics.get("default_highlight", {}).get("default_highlight_precision"),
                default_recall=metrics.get("default_highlight", {}).get("default_highlight_recall"),
                default_f1=metrics.get("default_highlight", {}).get("default_highlight_f1"),
                duration_delta=metrics.get("duration", {}).get("duration_delta"),
                evaluation_status=result.get("score", {}).get("evaluation_status", ""),
            )
        )

    lines.extend(["", "## 6. 失败与风险用例"])
    warning_lines: list[str] = []
    for result in results:
        case_id = result["case"].get("case_id", "")
        metrics = result.get("metrics", {})
        if result.get("run_status") == "failed":
            warning_lines.append(f"- {case_id}: Skill 运行失败")
        if not metrics.get("functional_completeness", {}).get("result_summary_exists"):
            warning_lines.append(f"- {case_id}: 缺少 result_summary.json")
        if _safe_float(metrics.get("must_avoid", {}).get("violation_rate")) > 0:
            warning_lines.append(f"- {case_id}: 命中了禁止出现的语义标签")
        if result["case"].get("annotation_coverage") == "uncovered":
            warning_lines.append(f"- {case_id}: 当前人工标注未覆盖该评测目标")
        if _safe_float(metrics.get("excluded_highlights", {}).get("high_score_excluded_count")) > 0:
            warning_lines.append(f"- {case_id}: 存在高分但未选入的候选高光")
        duration = metrics.get("duration", {})
        if _safe_float(duration.get("duration_score"), 10.0) < 10.0:
            warning_lines.append(f"- {case_id}: 最终时长与目标时长偏差较大")
    lines.extend(warning_lines or ["- 无"])

    lines.extend(
        [
            "",
            "## 7. 说明",
            "- 第一版使用 mock 后端做自动评测。",
            "- description judge 是启发式 mock，不是真实 LLM Judge。",
            "- uncovered 样本不纳入自动平均分。",
            "- default_highlight_score 只在 generic 指令下作为主依据。",
            "- specific/conflict 指令以 must_cover_tags / must_avoid_tags 为主。",
            "- GT 时间戳使用整数秒。",
            "- 匹配时允许 boundary_tolerance_seconds=1.0 的边界容忍。",
            "- IoU 和 overlap_ratio 是底层片段匹配工具。",
            "- Precision、Recall、F1 是 generic 默认高光评测中面向用户展示的主要语义指标。",
            "- 容忍窗口只影响 hit / miss 判断，不替代原始 IoU。",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _evaluate_case(
    case: dict[str, Any],
    annotation: dict[str, Any],
    *,
    output_dir: Path,
    backend: str,
    run_skill: bool,
    score_only: bool,
    skill_output_root: Path | None,
    dry_run: bool,
) -> dict[str, Any]:
    case_id = str(case["case_id"])
    case_run_root = (skill_output_root / case_id) if score_only and skill_output_root else output_dir / "runs" / case_id
    command = _build_run_skill_command(case, annotation, case_run_root, backend)
    started = time.time()
    completed_process: subprocess.CompletedProcess[str] | None = None

    if dry_run:
        print(shlex.join(command))
        result = {
            "case": case,
            "annotation": annotation,
            "command": command,
            "run_status": "dry_run",
            "elapsed_seconds": 0.0,
            "fallback_used": False,
            "metrics": {},
            "score": {"final_score": None, "evaluation_status": "dry_run", "score_components": {}},
        }
        return result

    if run_skill and not score_only:
        completed_process = subprocess.run(
            command,
            cwd=ROOT_DIR,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    outputs = _find_run_outputs(case_run_root)
    result_payload, raw_outputs = _result_payload_from_outputs(outputs)
    elapsed = round(time.time() - started, 3)
    status = result_payload.get("status")
    if completed_process is not None and completed_process.returncode != 0:
        run_status = "failed"
    elif status == "failed":
        run_status = "failed"
    elif result_payload.get("final_segments"):
        run_status = "success"
    else:
        run_status = "failed"

    scored = _score_case(case, annotation, result_payload, outputs)
    result = {
        "case": case,
        "annotation": {
            "video_id": annotation.get("video_id"),
            "video_path": annotation.get("video_path"),
            "video_type": annotation.get("video_type"),
            "semantic_segment_count": len(annotation.get("semantic_segments", [])),
        },
        "command": command,
        "run_status": run_status,
        "elapsed_seconds": elapsed,
        "fallback_used": _detect_fallback(outputs.get("run_log")),
        "output_paths": {key: str(value) if value else "" for key, value in outputs.items()},
        "result_payload": result_payload,
        "raw_outputs": raw_outputs,
        "metrics": scored["metrics"],
        "score": scored["score"],
    }
    if completed_process is not None:
        result["process_returncode"] = completed_process.returncode
        result["process_output_tail"] = completed_process.stdout[-4000:] if completed_process.stdout else ""
    return result


def _run_v4_eval(args: argparse.Namespace) -> int:
    if not args.run_skill and not args.score_only and not args.dry_run:
        raise SystemExit("V4 模式需要指定 --run_skill、--score_only 或 --dry_run")
    if args.gt_dir:
        if args.annotations:
            print("WARNING: 同时传入 --gt_dir 和 --annotations，已优先使用 --gt_dir。", file=sys.stderr)
        annotations = load_gt_dir(args.gt_dir)
        annotation_source = str(args.gt_dir)
    else:
        annotations = {item["video_id"]: item for item in _read_jsonl(args.annotations)}
        annotation_source = str(args.annotations)
    cases = _read_jsonl(args.cases)
    if args.limit is not None:
        cases = cases[: args.limit]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "cases").mkdir(parents=True, exist_ok=True)
    (args.output_dir / "runs").mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for case in cases:
        video_id = case.get("video_id")
        if video_id not in annotations:
            result = {
                "case": case,
                "annotation": {},
                "command": [],
                "run_status": "failed",
                "elapsed_seconds": 0.0,
                "fallback_used": False,
                "metrics": {},
                "score": {"final_score": None, "evaluation_status": "missing_annotation", "score_components": {}},
                "error": f"找不到 video_id={video_id} 的 annotation",
            }
        else:
            result = _evaluate_case(
                case,
                annotations[video_id],
                output_dir=args.output_dir,
                backend=args.backend,
                run_skill=args.run_skill,
                score_only=args.score_only,
                skill_output_root=args.skill_output_root,
                dry_run=args.dry_run,
            )
        results.append(result)
        _write_json(args.output_dir / "cases" / f"{case['case_id']}.result.json", result)

    csv_rows = [_csv_row_from_result(result) for result in results]
    _write_results_csv(args.output_dir / "results.csv", csv_rows)
    _write_eval_report(
        args.output_dir / "eval_report.md",
        backend=args.backend,
        cases_path=args.cases,
        annotations_path=annotation_source,
        output_dir=args.output_dir,
        results=results,
        dry_run=args.dry_run,
    )
    print(f"评测完成：{args.output_dir}")
    print(f"results.csv: {args.output_dir / 'results.csv'}")
    print(f"eval_report.md: {args.output_dir / 'eval_report.md'}")
    failed_count = sum(1 for result in results if result.get("run_status") == "failed")
    return 1 if failed_count and not args.dry_run else 0


def _run_legacy_eval(args: argparse.Namespace) -> int:
    if args.video_duration is None:
        raise SystemExit("旧 --plan_json 模式需要传入 --video_duration")
    config = load_config()
    plan = read_json(args.plan_json)
    resolved_target_duration = resolve_target_duration(plan, args.target_duration)
    metrics = compute_plan_metrics(plan, resolved_target_duration)
    validation = validate_plan(plan, args.video_duration, resolved_target_duration, config)
    print(
        json.dumps(
            {
                "metrics": metrics,
                "validation": validation,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if validation["ok"] else 1


def _run_auto_eval(args: argparse.Namespace) -> int:
    if not args.gt_dir:
        raise SystemExit("自动单条评测模式需要传入 --gt_dir")
    if not args.skill_output_dir:
        raise SystemExit("自动单条评测模式需要传入 --skill_output_dir")
    if not args.output_dir:
        raise SystemExit("自动单条评测模式需要传入 --output_dir")
    skill_config = load_config()
    llm_config = skill_config.get("llm", {}) if isinstance(skill_config, dict) else {}
    resolver_model = args.resolver_model or llm_config.get("model") or ArkResolverConfig().model
    resolver_base_url = args.resolver_base_url or llm_config.get("base_url") or ArkResolverConfig().base_url
    resolver_api_key_env = args.resolver_api_key_env or llm_config.get("api_key_env") or ArkResolverConfig().api_key_env
    resolver_timeout_seconds = (
        args.resolver_timeout_seconds
        or llm_config.get("timeout_seconds")
        or ArkResolverConfig().timeout_seconds
    )
    judge_model = args.judge_model or resolver_model
    judge_base_url = args.judge_base_url or resolver_base_url
    judge_api_key_env = args.judge_api_key_env or resolver_api_key_env
    judge_timeout_seconds = args.judge_timeout_seconds or resolver_timeout_seconds
    result = run_auto_eval(
        AutoEvalConfig(
            input_video=args.input_video,
            instruction=args.instruction,
            target_duration=args.target_duration,
            skill_output_dir=args.skill_output_dir,
            gt_dir=args.gt_dir,
            output_dir=args.output_dir,
            resolver_config=ArkResolverConfig(
                model=str(resolver_model),
                base_url=str(resolver_base_url),
                api_key_env=str(resolver_api_key_env),
                timeout_seconds=int(resolver_timeout_seconds),
            ),
            generated_case_json=args.generated_case_json,
            judge_video_url=args.judge_video_url,
            aesthetic_judge_config=ArkAestheticJudgeConfig(
                model=str(judge_model),
                base_url=str(judge_base_url),
                api_key_env=str(judge_api_key_env),
                timeout_seconds=int(judge_timeout_seconds),
            ),
            judge_repeats=int(args.judge_repeats),
        )
    )
    print(f"自动评测完成：{args.output_dir}")
    print(f"evaluation_result.json: {args.output_dir / 'evaluation_result.json'}")
    print(f"eval_report.md: {args.output_dir / 'eval_report.md'}")
    return 0 if result.get("evaluation_status") != "resolver_failed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="评估 ClawCut 结构化剪辑方案或运行 V4 mock 评测。")
    parser.add_argument("--plan_json", type=Path)
    parser.add_argument("--video_duration", type=float)
    parser.add_argument("--target_duration", type=float, default=None)

    parser.add_argument("--input_video", type=Path)
    parser.add_argument("--instruction")
    parser.add_argument("--skill_output_dir", type=Path)
    parser.add_argument("--resolver_model", default=None)
    parser.add_argument("--resolver_base_url", default=None)
    parser.add_argument("--resolver_api_key_env", default=None)
    parser.add_argument("--resolver_timeout_seconds", type=int, default=None)
    parser.add_argument("--generated_case_json", type=Path)
    parser.add_argument("--judge_video_url")
    parser.add_argument("--judge_model", default=None)
    parser.add_argument("--judge_base_url", default=None)
    parser.add_argument("--judge_api_key_env", default="ARK_API_KEY")
    parser.add_argument("--judge_timeout_seconds", type=int, default=None)
    parser.add_argument("--judge_repeats", type=int, default=1)

    parser.add_argument("--cases", type=Path)
    parser.add_argument("--annotations", type=Path)
    parser.add_argument(
        "--gt_dir",
        type=Path,
        help="按视频名称读取独立 GT JSON 文件的目录，例如 data/eval",
    )
    parser.add_argument("--output_dir", type=Path, default=Path("eval_outputs/mock_v1"))
    parser.add_argument("--backend", choices=["mock", "ark"], default="mock")
    parser.add_argument("--run_skill", action="store_true")
    parser.add_argument("--score_only", action="store_true")
    parser.add_argument("--skill_output_root", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry_run", action="store_true")
    args = parser.parse_args()

    if args.plan_json:
        return _run_legacy_eval(args)
    if args.input_video or args.instruction:
        if not args.input_video or not args.instruction:
            raise SystemExit("自动单条评测模式需要同时传入 --input_video 和 --instruction")
        return _run_auto_eval(args)
    if not args.cases:
        raise SystemExit("V4 模式需要传入 --cases，或使用旧模式 --plan_json")
    if not args.gt_dir and not args.annotations:
        raise SystemExit("V4 模式需要传入 --gt_dir 或旧版 --annotations，或使用旧模式 --plan_json")
    return _run_v4_eval(args)


if __name__ == "__main__":
    raise SystemExit(main())
