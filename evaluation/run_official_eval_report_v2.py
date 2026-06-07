from __future__ import annotations

import argparse
import csv
import html
import json
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

try:
    from .ark_aesthetic_judge_client import ArkAestheticJudgeConfig
    from .ark_resolver_client import ArkResolverConfig
    from .auto_eval import AutoEvalConfig, run_auto_eval
    from .dover_quality import build_dover_config
    from .issue_summary import ISSUE_LABELS, build_issue_summary
    from .tos_uploader import build_tos_upload_config
except ImportError:  # pragma: no cover - script mode
    from ark_aesthetic_judge_client import ArkAestheticJudgeConfig
    from ark_resolver_client import ArkResolverConfig
    from auto_eval import AutoEvalConfig, run_auto_eval
    from dover_quality import build_dover_config
    from issue_summary import ISSUE_LABELS, build_issue_summary
    from tos_uploader import build_tos_upload_config


SUMMARY_SCHEMA_VERSION = "official_eval_summary_v2"
EVALUATION_RESULT_SCHEMA_VERSION = "evaluation_result_v2"
XLSX_SHEETS = [
    "全部 Case 结果",
    "内容选择明细",
    "成片体验明细",
    "技术质量明细",
    "耗时与 Token 明细",
    "指标说明",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text:
                rows.append(json.loads(text))
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_path_maps(values: list[str] | None) -> dict[str, str]:
    path_map: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(f"--path-map must use FROM=TO format: {value}")
        source, target = value.split("=", 1)
        path_map[source.strip().rstrip("/")] = target.strip().rstrip("/")
    return path_map


def map_path(value: Any, path_map: dict[str, str] | None = None) -> Path:
    text = str(value)
    for source_prefix, target_prefix in (path_map or {}).items():
        if text == source_prefix:
            return Path(target_prefix)
        if text.startswith(source_prefix + "/"):
            return Path(target_prefix + text[len(source_prefix) :])
    return Path(text)


def sum_numbers(*values: Any) -> int | float | None:
    numbers = [value for value in values if isinstance(value, (int, float))]
    return sum(numbers) if numbers else None


def result_summary_from(result: dict[str, Any]) -> dict[str, Any]:
    artifact = result.get("artifact_validation") if isinstance(result.get("artifact_validation"), dict) else {}
    return artifact.get("result_summary") if isinstance(artifact.get("result_summary"), dict) else {}


def resolver_usage_from(result: dict[str, Any]) -> dict[str, Any]:
    metadata = result.get("resolver_metadata") if isinstance(result.get("resolver_metadata"), dict) else {}
    return metadata.get("resolver_usage") if isinstance(metadata.get("resolver_usage"), dict) else {}


def judge_usage_from(result: dict[str, Any]) -> dict[str, Any]:
    aesthetic = result.get("aesthetic_judge") if isinstance(result.get("aesthetic_judge"), dict) else {}
    metadata = aesthetic.get("judge_metadata")
    if not isinstance(metadata, list):
        metadata = []
    usage_items = [
        item.get("aesthetic_judge_usage")
        for item in metadata
        if isinstance(item, dict) and isinstance(item.get("aesthetic_judge_usage"), dict)
    ]
    latency = sum_numbers(*[
        item.get("aesthetic_judge_latency_seconds")
        for item in metadata
        if isinstance(item, dict)
    ])
    return {
        "latency_seconds": round(float(latency), 3) if latency is not None else None,
        "prompt_tokens": sum_numbers(*[usage.get("prompt_tokens") for usage in usage_items]),
        "completion_tokens": sum_numbers(*[usage.get("completion_tokens") for usage in usage_items]),
        "total_tokens": sum_numbers(*[usage.get("total_tokens") for usage in usage_items]),
    }


def build_consumption(result: dict[str, Any]) -> dict[str, Any]:
    summary = result_summary_from(result)
    skill_consumption = summary.get("skill_consumption") if isinstance(summary.get("skill_consumption"), dict) else {}
    resolver_metadata = result.get("resolver_metadata") if isinstance(result.get("resolver_metadata"), dict) else {}
    resolver_usage = resolver_usage_from(result)
    judge_usage = judge_usage_from(result)
    skill_total = skill_consumption.get("skill_llm_total_tokens", summary.get("skill_llm_total_tokens"))
    resolver_total = resolver_usage.get("total_tokens")
    judge_total = judge_usage.get("total_tokens")
    evaluation_total = sum_numbers(resolver_total, judge_total)
    end_to_end_total = sum_numbers(skill_total, resolver_total, judge_total)
    skill_elapsed = skill_consumption.get("skill_run_elapsed_seconds", summary.get("skill_run_elapsed_seconds"))
    evaluation_elapsed = result.get("evaluation_elapsed_seconds")
    return {
        "video_editing": {
            "elapsed_seconds": skill_elapsed,
            "preview_generation_seconds": skill_consumption.get("preview_generation_seconds", summary.get("preview_generation_seconds")),
            "skill_llm_latency_seconds": skill_consumption.get("skill_llm_latency_seconds", summary.get("skill_llm_latency_seconds")),
            "ffmpeg_render_seconds": skill_consumption.get("ffmpeg_render_seconds", summary.get("ffmpeg_render_seconds")),
            "llm_prompt_tokens": skill_consumption.get("skill_llm_prompt_tokens", summary.get("skill_llm_prompt_tokens")),
            "llm_completion_tokens": skill_consumption.get("skill_llm_completion_tokens", summary.get("skill_llm_completion_tokens")),
            "llm_total_tokens": skill_total,
            "skill_llm_attempt_count": skill_consumption.get("skill_llm_attempt_count", summary.get("skill_llm_attempt_count")),
        },
        "evaluation": {
            "elapsed_seconds": evaluation_elapsed,
            "resolver_latency_seconds": resolver_metadata.get("resolver_latency_seconds"),
            "resolver_prompt_tokens": resolver_usage.get("prompt_tokens"),
            "resolver_completion_tokens": resolver_usage.get("completion_tokens"),
            "resolver_total_tokens": resolver_total,
            "judge_latency_seconds": judge_usage.get("latency_seconds"),
            "judge_prompt_tokens": judge_usage.get("prompt_tokens"),
            "judge_completion_tokens": judge_usage.get("completion_tokens"),
            "judge_total_tokens": judge_total,
            "llm_total_tokens": evaluation_total,
        },
        "end_to_end": {
            "elapsed_seconds": sum_numbers(skill_elapsed, evaluation_elapsed),
            "llm_total_tokens": end_to_end_total,
        },
    }


def build_case_metadata(case: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "test_type",
        "primary_capability",
        "tested_capability",
        "challenge_tags",
        "execution_tier",
        "include_in_official_score",
        "priority",
    ]
    return {key: case.get(key) for key in keys if key in case}


def official_eligibility(result: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if result.get("evaluation_status") != "scored_complete":
        reasons.append(str(result.get("evaluation_status") or "not_scored_complete"))
    artifact = result.get("artifact_validation") if isinstance(result.get("artifact_validation"), dict) else {}
    if artifact.get("fallback_used"):
        reasons.append("skill_fallback_used")
    if artifact.get("skill_backend_used") not in {None, "ark"}:
        reasons.append("skill_backend_not_ark")
    if artifact and not artifact.get("artifact_validation_passed"):
        reasons.append("artifact_validation_failed")
    technical = result.get("technical_quality") if isinstance(result.get("technical_quality"), dict) else {}
    if technical and technical.get("technical_quality_passed") is False:
        reasons.append("technical_quality_failed")
    return {"eligible": not reasons, "exclusion_reasons": reasons}


def enrich_result_v2(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(result)
    enriched["evaluation_result_schema_version"] = EVALUATION_RESULT_SCHEMA_VERSION
    enriched["case_id"] = case.get("case_id")
    enriched["case_metadata"] = build_case_metadata(case)
    enriched["official_score_eligibility"] = official_eligibility(enriched)
    enriched["failure_info"] = {
        "error_type": enriched.get("error_type"),
        "error_message": enriched.get("error_message"),
    }
    artifact = enriched.get("artifact_validation") if isinstance(enriched.get("artifact_validation"), dict) else {}
    enriched["fallback_info"] = {
        "fallback_used": artifact.get("fallback_used"),
        "skill_backend_used": artifact.get("skill_backend_used"),
    }
    enriched["manual_review"] = {
        "required": enriched.get("evaluation_status") in {"manual_review_required", "judge_manual_review_required"},
        "recommended": bool((enriched.get("editing_experience") or {}).get("manual_review_recommended")) if isinstance(enriched.get("editing_experience"), dict) else False,
    }
    enriched["issue_summary"] = build_issue_summary(enriched)
    enriched["consumption"] = build_consumption(enriched)
    return enriched


def run_case(
    *,
    case: dict[str, Any],
    index: int,
    args: argparse.Namespace,
    path_map: dict[str, str],
) -> dict[str, Any]:
    case_id = str(case.get("case_id") or f"case_{index:03d}")
    run_dir = args.output_dir / "runs" / case_id
    result_path = run_dir / "evaluation_result.json"
    if args.resume and result_path.exists() and not args.retry_failed:
        return json.loads(result_path.read_text(encoding="utf-8"))
    if args.retry_failed and result_path.exists():
        existing = json.loads(result_path.read_text(encoding="utf-8"))
        if existing.get("official_score_eligibility", {}).get("eligible"):
            return existing
    result = run_auto_eval(
        AutoEvalConfig(
            input_video=map_path(case["input_video"], path_map),
            instruction=str(case["instruction"]),
            target_duration=case.get("target_duration"),
            skill_output_dir=map_path(case["skill_output_dir"], path_map),
            gt_dir=args.gt_dir,
            output_dir=run_dir,
            resolver_config=ArkResolverConfig(),
            judge_video_url=case.get("judge_video_url"),
            aesthetic_judge_config=ArkAestheticJudgeConfig(),
            dover_config=build_dover_config(enabled=False),
            technical_quality_config=args.technical_quality_config,
            auto_upload_judge_video=bool(args.auto_upload_judge_video),
            tos_upload_config=build_tos_upload_config(
                enabled=bool(args.auto_upload_judge_video),
                bucket=args.tos_bucket,
                region=args.tos_region,
                endpoint=args.tos_endpoint,
                key_prefix=args.tos_key_prefix,
                presign_expires_seconds=args.tos_presign_expires_seconds,
            ),
            path_map=path_map,
            eval_run_id=args.output_dir.name,
            case_id=case_id,
            skill_run_id=str(case.get("skill_run_id") or Path(str(case.get("skill_output_dir") or "")).name or "run_01"),
        )
    )
    enriched = enrich_result_v2(case, result)
    write_json(result_path, enriched)
    return enriched


def load_existing_result(case: dict[str, Any], index: int, output_dir: Path) -> dict[str, Any]:
    case_id = str(case.get("case_id") or f"case_{index:03d}")
    path = output_dir / "runs" / case_id / "evaluation_result.json"
    if not path.exists():
        return enrich_result_v2(case, {
            "evaluation_status": "missing_evaluation_result",
            "evaluation_scope": "failed",
            "selection_score_v1": None,
            "editing_experience_score_v1": None,
            "aesthetic_score_v1": None,
            "final_score_v2": None,
            "error_type": "MissingEvaluationResult",
            "error_message": f"evaluation_result.json not found: {path}",
        })
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if payload.get("evaluation_result_schema_version") == EVALUATION_RESULT_SCHEMA_VERSION else enrich_result_v2(case, payload)


def flat_case_row(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    consumption = result.get("consumption") if isinstance(result.get("consumption"), dict) else {}
    editing = consumption.get("video_editing", {}) if isinstance(consumption.get("video_editing"), dict) else {}
    evaluation = consumption.get("evaluation", {}) if isinstance(consumption.get("evaluation"), dict) else {}
    e2e = consumption.get("end_to_end", {}) if isinstance(consumption.get("end_to_end"), dict) else {}
    technical = result.get("technical_quality") if isinstance(result.get("technical_quality"), dict) else {}
    issues = result.get("issue_summary") if isinstance(result.get("issue_summary"), list) else []
    return {
        "case_id": result.get("case_id") or case.get("case_id"),
        "video_id": result.get("video_id") or case.get("video_id"),
        "test_type": case.get("test_type"),
        "instruction": case.get("instruction"),
        "evaluation_status": result.get("evaluation_status"),
        "official_eligible": (result.get("official_score_eligibility") or {}).get("eligible"),
        "selection_score_v1": result.get("selection_score_v1"),
        "editing_experience_score_v1": result.get("editing_experience_score_v1"),
        "final_score_v2": result.get("final_score_v2"),
        "technical_quality_passed": technical.get("technical_quality_passed"),
        "issues": "；".join(issue.get("label", "") for issue in issues),
        "video_editing_elapsed_seconds": editing.get("elapsed_seconds"),
        "preview_generation_seconds": editing.get("preview_generation_seconds"),
        "skill_llm_latency_seconds": editing.get("skill_llm_latency_seconds"),
        "ffmpeg_render_seconds": editing.get("ffmpeg_render_seconds"),
        "skill_llm_prompt_tokens": editing.get("llm_prompt_tokens"),
        "skill_llm_completion_tokens": editing.get("llm_completion_tokens"),
        "skill_llm_total_tokens": editing.get("llm_total_tokens"),
        "evaluation_elapsed_seconds": evaluation.get("elapsed_seconds"),
        "resolver_latency_seconds": evaluation.get("resolver_latency_seconds"),
        "resolver_total_tokens": evaluation.get("resolver_total_tokens"),
        "judge_latency_seconds": evaluation.get("judge_latency_seconds"),
        "judge_total_tokens": evaluation.get("judge_total_tokens"),
        "end_to_end_elapsed_seconds": e2e.get("elapsed_seconds"),
        "end_to_end_llm_total_tokens": e2e.get("llm_total_tokens"),
    }


def average(values: list[Any]) -> float | None:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return round(sum(numbers) / len(numbers), 3) if numbers else None


def total(values: list[Any]) -> float | None:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return round(sum(numbers), 3) if numbers else None


def build_summary(cases: list[dict[str, Any]], results: list[dict[str, Any]], rows: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    eligible = [result for result in results if (result.get("official_score_eligibility") or {}).get("eligible")]
    status_counts: dict[str, int] = {}
    issue_counts: dict[str, int] = {}
    for result in results:
        status = str(result.get("evaluation_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        for issue in result.get("issue_summary", []) or []:
            issue_type = str(issue.get("issue_type"))
            issue_counts[issue_type] = issue_counts.get(issue_type, 0) + 1
    return {
        "summary_schema_version": SUMMARY_SCHEMA_VERSION,
        "run_metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "case_count": len(cases),
            "output_dir": str(output_dir),
        },
        "execution_overview": {
            "planned_case_count": len(cases),
            "status_counts": status_counts,
            "official_scored_count": len(eligible),
        },
        "overall_scores": {
            "selection_score_v1_avg": average([item.get("selection_score_v1") for item in eligible]),
            "editing_experience_score_v1_avg": average([item.get("editing_experience_score_v1") for item in eligible]),
            "final_score_v2_avg": average([item.get("final_score_v2") for item in eligible]),
        },
        "consumption_overview": {
            "video_editing_elapsed_total": total([row.get("video_editing_elapsed_seconds") for row in rows]),
            "video_editing_elapsed_avg": average([row.get("video_editing_elapsed_seconds") for row in rows]),
            "skill_llm_total_tokens_total": total([row.get("skill_llm_total_tokens") for row in rows]),
            "evaluation_elapsed_total": total([row.get("evaluation_elapsed_seconds") for row in rows]),
            "evaluation_elapsed_avg": average([row.get("evaluation_elapsed_seconds") for row in rows]),
            "resolver_total_tokens_total": total([row.get("resolver_total_tokens") for row in rows]),
            "judge_total_tokens_total": total([row.get("judge_total_tokens") for row in rows]),
            "end_to_end_elapsed_total": total([row.get("end_to_end_elapsed_seconds") for row in rows]),
            "end_to_end_llm_total_tokens_total": total([row.get("end_to_end_llm_total_tokens") for row in rows]),
        },
        "failure_summary": {
            "failed_count": sum(1 for result in results if str(result.get("evaluation_status", "")).endswith("failed")),
            "statuses": status_counts,
        },
        "fallback_summary": {
            "skill_fallback_count": sum(1 for result in results if (result.get("fallback_info") or {}).get("fallback_used")),
        },
        "manual_review_summary": {
            "required_count": sum(1 for result in results if (result.get("manual_review") or {}).get("required")),
            "recommended_count": sum(1 for result in results if (result.get("manual_review") or {}).get("recommended")),
        },
        "breakdowns": {
            "by_issue": issue_counts,
            "by_status": status_counts,
            "by_test_type": _breakdown_by(rows, "test_type"),
        },
        "case_index": rows,
        "special_reports_overview": {},
        "artifact_paths": {
            "case_results_csv": str(output_dir / "case_results.csv"),
            "case_results_xlsx": str(output_dir / "case_results.xlsx"),
            "report_html": str(output_dir / "report.html"),
            "cases_dir": str(output_dir / "cases"),
        },
    }


def _breakdown_by(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for row in rows:
        key = str(row.get(field) or "unknown")
        bucket = result.setdefault(key, {"case_count": 0, "eligible_count": 0, "final_score_v2_avg": None, "_scores": []})
        bucket["case_count"] += 1
        if row.get("official_eligible"):
            bucket["eligible_count"] += 1
            bucket["_scores"].append(row.get("final_score_v2"))
    for bucket in result.values():
        bucket["final_score_v2_avg"] = average(bucket.pop("_scores"))
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else ["case_id"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_xlsx(path: Path, rows: list[dict[str, Any]]) -> None:
    sheet_rows = {
        "全部 Case 结果": rows,
        "内容选择明细": _select_columns(rows, ["case_id", "video_id", "test_type", "selection_score_v1", "issues"]),
        "成片体验明细": _select_columns(rows, ["case_id", "editing_experience_score_v1", "final_score_v2", "issues"]),
        "技术质量明细": _select_columns(rows, ["case_id", "technical_quality_passed", "issues"]),
        "耗时与 Token 明细": _select_columns(rows, [
            "case_id",
            "video_editing_elapsed_seconds",
            "preview_generation_seconds",
            "skill_llm_latency_seconds",
            "ffmpeg_render_seconds",
            "skill_llm_prompt_tokens",
            "skill_llm_completion_tokens",
            "skill_llm_total_tokens",
            "evaluation_elapsed_seconds",
            "resolver_latency_seconds",
            "resolver_total_tokens",
            "judge_latency_seconds",
            "judge_total_tokens",
            "end_to_end_elapsed_seconds",
            "end_to_end_llm_total_tokens",
        ]),
        "指标说明": [
            {"指标": "official_eligible", "说明": "只有完整评分且无排除原因的 case 进入正式平均分。"},
            {"指标": "selection_score_v1", "说明": "内容选择评分。"},
            {"指标": "final_score_v2", "说明": "0.70 × selection_score_v1 + 0.30 × editing_experience_score_v1。"},
        ],
    }
    _write_minimal_xlsx(path, sheet_rows)


def _select_columns(rows: list[dict[str, Any]], fields: list[str]) -> list[dict[str, Any]]:
    return [{field: row.get(field) for field in fields} for row in rows]


def _write_minimal_xlsx(path: Path, sheets: dict[str, list[dict[str, Any]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _xlsx_content_types(len(sheets)))
        archive.writestr("_rels/.rels", _xlsx_root_rels())
        archive.writestr("xl/workbook.xml", _xlsx_workbook(list(sheets)))
        archive.writestr("xl/_rels/workbook.xml.rels", _xlsx_workbook_rels(len(sheets)))
        for index, (_, rows) in enumerate(sheets.items(), start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _xlsx_sheet(rows))


def _xlsx_content_types(sheet_count: int) -> str:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, sheet_count + 1)
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>{overrides}</Types>'


def _xlsx_root_rels() -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'


def _xlsx_workbook(names: list[str]) -> str:
    sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(names, start=1)
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{sheets}</sheets></workbook>'


def _xlsx_workbook_rels(sheet_count: int) -> str:
    relationships = "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, sheet_count + 1)
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{relationships}</Relationships>'


def _xlsx_sheet(rows: list[dict[str, Any]]) -> str:
    fields = list(rows[0].keys()) if rows else ["case_id"]
    all_rows = [fields] + [[row.get(field) for field in fields] for row in rows]
    xml_rows = []
    for row_index, values in enumerate(all_rows, start=1):
        cells = []
        for col_index, value in enumerate(values, start=1):
            ref = f"{_col_name(col_index)}{row_index}"
            text = "" if value is None else str(value)
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(text)}</t></is></c>')
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>'


def _col_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def write_html_reports(output_dir: Path, summary: dict[str, Any], rows: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    cases_dir = output_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    data_json = json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False)
    html_doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>ClawCut V2 正式评测报告</title><style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:24px;color:#1f2937}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}.card{{border:1px solid #ddd;border-radius:8px;padding:12px;background:#fff}}table{{border-collapse:collapse;width:100%;margin-top:16px}}td,th{{border:1px solid #ddd;padding:6px;text-align:left}}#drawer{{position:fixed;right:0;top:0;bottom:0;width:420px;max-width:90vw;background:#fff;border-left:1px solid #ccc;overflow:auto;padding:16px;display:none}}button{{cursor:pointer}}</style></head><body>
<h1>ClawCut V2 正式评测报告</h1>
<script id="report-data" type="application/json">{html.escape(data_json)}</script>
<section><h2>本轮评测信息</h2><p>Case 数：{summary['run_metadata']['case_count']}；生成时间：{html.escape(summary['run_metadata']['generated_at'])}</p></section>
<section class="grid">
<button class="card" onclick="showCases('all')">总体执行情况<br><strong>{summary['execution_overview']['planned_case_count']}</strong></button>
<button class="card" onclick="showCases('eligible')">正式评分样本<br><strong>{summary['execution_overview']['official_scored_count']}</strong></button>
<button class="card" onclick="showCases('failed')">执行失败/未完成<br><strong>{summary['failure_summary']['failed_count']}</strong></button>
<button class="card" onclick="showCases('fallback')">Fallback<br><strong>{summary['fallback_summary']['skill_fallback_count']}</strong></button>
</section>
<section><h2>整体剪辑效果</h2><pre>{html.escape(json.dumps(summary['overall_scores'], ensure_ascii=False, indent=2))}</pre></section>
<section><h2>耗时与 Token 消耗</h2><pre>{html.escape(json.dumps(summary['consumption_overview'], ensure_ascii=False, indent=2))}</pre></section>
<section><h2>不同用户要求下的剪辑表现</h2><pre>{html.escape(json.dumps(summary['breakdowns']['by_test_type'], ensure_ascii=False, indent=2))}</pre></section>
<section><h2>不同视频场景下的剪辑表现</h2><p>第一版按 test_type 汇总；后续可接入 video_scenario 字段。</p></section>
<section><h2>本轮主要问题</h2><pre>{html.escape(json.dumps(summary['breakdowns']['by_issue'], ensure_ascii=False, indent=2))}</pre></section>
<section><h2>专项测试概况</h2><pre>{html.escape(json.dumps(summary.get('special_reports_overview', {}), ensure_ascii=False, indent=2))}</pre></section>
<table><thead><tr><th>case_id</th><th>status</th><th>selection</th><th>editing</th><th>final</th><th>issues</th></tr></thead><tbody>
{''.join(f"<tr><td><a href='cases/{html.escape(str(row['case_id']))}.html'>{html.escape(str(row['case_id']))}</a></td><td>{html.escape(str(row.get('evaluation_status')))}</td><td>{html.escape(str(row.get('selection_score_v1')))}</td><td>{html.escape(str(row.get('editing_experience_score_v1')))}</td><td>{html.escape(str(row.get('final_score_v2')))}</td><td>{html.escape(str(row.get('issues')))}</td></tr>" for row in rows)}
</tbody></table><aside id="drawer"></aside><script>
const data = JSON.parse(document.getElementById('report-data').textContent);
function showCases(kind){{const rows=data.rows.filter(r=>kind==='all'||(kind==='eligible'&&r.official_eligible)||(kind==='failed'&&String(r.evaluation_status).includes('failed'))||(kind==='fallback'&&String(r.issues).includes('Ark')));const d=document.getElementById('drawer');d.style.display='block';d.innerHTML='<button onclick="drawer.style.display=\\'none\\'">关闭</button><h2>Case 列表</h2>'+rows.map(r=>'<p>'+r.case_id+' - '+r.evaluation_status+'</p>').join('');}}
</script></body></html>"""
    (output_dir / "report.html").write_text(html_doc, encoding="utf-8")
    for row, result in zip(rows, results):
        case_html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{html.escape(str(row['case_id']))}</title></head><body><h1>{html.escape(str(row['case_id']))}</h1><pre>{html.escape(json.dumps(result, ensure_ascii=False, indent=2))}</pre></body></html>"""
        (cases_dir / f"{row['case_id']}.html").write_text(case_html, encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    path_map = parse_path_maps(args.path_map)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cases = read_jsonl(args.cases)
    results: list[dict[str, Any]] = []
    started = time.monotonic()
    for index, case in enumerate(cases, start=1):
        if args.mode == "report-only":
            result = load_existing_result(case, index, args.output_dir)
        else:
            result = run_case(case=case, index=index, args=args, path_map=path_map)
        results.append(result)
    rows = [flat_case_row(case, result) for case, result in zip(cases, results)]
    summary = build_summary(cases, results, rows, args.output_dir)
    summary["run_metadata"]["elapsed_seconds"] = round(time.monotonic() - started, 3)
    write_json(args.output_dir / "summary.json", summary)
    write_csv(args.output_dir / "case_results.csv", rows)
    write_xlsx(args.output_dir / "case_results.xlsx", rows)
    write_html_reports(args.output_dir, summary, rows, results)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ClawCut official V2 evaluation and report generation.")
    parser.add_argument("--mode", choices=["evaluate", "report-only"], default="evaluate")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--gt-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--path-map", action="append", default=[])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--technical_quality_config", type=Path, default=Path("evaluation/config/default.yaml"))
    parser.add_argument("--auto_upload_judge_video", action="store_true")
    parser.add_argument("--tos_bucket", default=None)
    parser.add_argument("--tos_region", default=None)
    parser.add_argument("--tos_endpoint", default=None)
    parser.add_argument("--tos_key_prefix", default=None)
    parser.add_argument("--tos_presign_expires_seconds", type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    summary = run(parse_args(argv))
    print(f"official eval report v2 完成：{summary['artifact_paths']['report_html']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
