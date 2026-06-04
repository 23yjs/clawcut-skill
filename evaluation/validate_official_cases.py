from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from .artifact_validation import validate_skill_artifacts
except ImportError:  # pragma: no cover - script mode
    from artifact_validation import validate_skill_artifacts


REQUIRED_FIELDS = {
    "case_id",
    "video_id",
    "instruction",
    "input_video",
    "skill_output_dir",
    "test_type",
    "priority",
}

CSV_FIELDS = [
    "case_index",
    "case_id",
    "video_id",
    "priority",
    "test_type",
    "status",
    "skill_backend_used",
    "fallback_used",
    "result_summary_exists",
    "segments_json_exists",
    "highlight_video_exists",
    "run_log_exists",
    "input_video_match",
    "instruction_match",
    "target_duration_match",
    "error_message",
]

READY_CASES_FILENAME = "official_ready_cases.jsonl"
DIAGNOSTIC_CASES_FILENAME = "official_diagnostic_cases.jsonl"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError(f"line {line_number}: case must be object")
            rows.append(payload)
    return rows


def _missing_required(case: dict[str, Any]) -> list[str]:
    return sorted(field for field in REQUIRED_FIELDS if not case.get(field))


def classify_case_readiness(case: dict[str, Any]) -> dict[str, Any]:
    missing = _missing_required(case)
    if missing:
        return {
            "case_id": case.get("case_id", ""),
            "video_id": case.get("video_id", ""),
            "priority": case.get("priority", ""),
            "test_type": case.get("test_type", ""),
            "status": "invalid_case",
            "error_message": "缺少字段：" + ", ".join(missing),
        }

    validation = validate_skill_artifacts(
        input_video=Path(str(case["input_video"])),
        instruction=str(case["instruction"]),
        target_duration=case.get("target_duration"),
        skill_output_dir=Path(str(case["skill_output_dir"])),
    )
    errors = validation.get("artifact_validation_errors") or []
    if not validation.get("result_summary_exists") or not validation.get("segments_json_exists") or not validation.get("highlight_video_exists"):
        status = "missing_artifacts"
    elif validation.get("fallback_used") or str(validation.get("skill_backend_used")).lower() == "mock":
        status = "diagnostic_fallback"
    elif validation.get("artifact_validation_passed"):
        status = "ready"
    else:
        status = "invalid_artifacts"

    return {
        "case_id": case["case_id"],
        "video_id": case["video_id"],
        "priority": case.get("priority", ""),
        "test_type": case.get("test_type", ""),
        "status": status,
        "skill_backend_used": validation.get("skill_backend_used"),
        "fallback_used": validation.get("fallback_used"),
        "result_summary_exists": validation.get("result_summary_exists"),
        "segments_json_exists": validation.get("segments_json_exists"),
        "highlight_video_exists": validation.get("highlight_video_exists"),
        "run_log_exists": validation.get("run_log_exists"),
        "input_video_match": validation.get("input_video_match"),
        "instruction_match": validation.get("instruction_match"),
        "target_duration_match": validation.get("target_duration_match"),
        "error_message": "; ".join(errors),
    }


def build_readiness_report(cases: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for case_index, case in enumerate(cases, start=1):
        row = classify_case_readiness(case)
        row["case_index"] = case_index
        rows.append(row)
    counts = Counter(row["status"] for row in rows)
    by_priority = {
        priority: dict(Counter(row["status"] for row in rows if row.get("priority") == priority))
        for priority in sorted({str(row.get("priority") or "") for row in rows})
    }
    return {
        "case_count": len(cases),
        "status_counts": dict(sorted(counts.items())),
        "by_priority": by_priority,
        "ready_for_official_eval": counts.get("ready", 0),
        "diagnostic_case_count": counts.get("diagnostic_fallback", 0),
        "not_ready_count": len(cases) - counts.get("ready", 0),
        "rows": rows,
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _select_cases_by_status(
    cases: list[dict[str, Any]],
    report: dict[str, Any],
    statuses: set[str],
) -> list[dict[str, Any]]:
    selected_indexes = {
        int(row["case_index"])
        for row in report["rows"]
        if row.get("status") in statuses and row.get("case_index")
    }
    return [case for case_index, case in enumerate(cases, start=1) if case_index in selected_indexes]


def write_readiness_outputs(report: dict[str, Any], output_dir: Path, cases: list[dict[str, Any]] | None = None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "official_case_readiness.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if cases is not None:
        ready_cases = _select_cases_by_status(cases, report, {"ready"})
        diagnostic_cases = _select_cases_by_status(cases, report, {"diagnostic_fallback"})
        _write_jsonl(output_dir / READY_CASES_FILENAME, ready_cases)
        _write_jsonl(output_dir / DIAGNOSTIC_CASES_FILENAME, diagnostic_cases)
    with (output_dir / "official_case_readiness.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in report["rows"]:
            writer.writerow({field: row.get(field) for field in CSV_FIELDS})
    lines = [
        "# ClawCut Official Case Readiness",
        "",
        f"- case_count: {report['case_count']}",
        f"- ready_for_official_eval: {report['ready_for_official_eval']}",
        f"- diagnostic_case_count: {report['diagnostic_case_count']}",
        f"- not_ready_count: {report['not_ready_count']}",
        f"- ready_cases_jsonl: {READY_CASES_FILENAME}",
        f"- diagnostic_cases_jsonl: {DIAGNOSTIC_CASES_FILENAME}",
        "",
        "## Status Counts",
        *(f"- {key}: {value}" for key, value in report["status_counts"].items()),
        "",
        "| case_id | priority | test_type | status | error_message |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row.get('case_id')} | {row.get('priority')} | {row.get('test_type')} | "
            f"{row.get('status')} | {row.get('error_message') or ''} |"
        )
    (output_dir / "official_case_readiness.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate official ClawCut cases before running batch evaluation.")
    parser.add_argument("--cases", type=Path, default=Path("data/eval/cases.official.v1.jsonl"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args(argv)
    cases = read_jsonl(args.cases)
    report = build_readiness_report(cases)
    write_readiness_outputs(report, args.output_dir, cases)
    print(f"official case 预检完成：{args.output_dir}")
    if args.require_ready and report["not_ready_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
