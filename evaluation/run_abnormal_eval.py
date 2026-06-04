from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {
    "case_id",
    "abnormal_type",
    "description",
    "expected_error_type",
    "expected_behavior",
    "should_enter_official_scoring",
}


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


def validate_abnormal_cases(cases: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for index, case in enumerate(cases, start=1):
        case_id = str(case.get("case_id") or f"line_{index}")
        missing = sorted(REQUIRED_FIELDS - set(case))
        if missing:
            errors.append(f"{case_id}: missing {', '.join(missing)}")
        if case_id in seen:
            errors.append(f"{case_id}: duplicate case_id")
        seen.add(case_id)
        if case.get("should_enter_official_scoring") is not False:
            errors.append(f"{case_id}: abnormal case must not enter official scoring")
        if case.get("abnormal_type") == "no_audio_video" and case.get("expected_error_type") != "none":
            errors.append(f"{case_id}: no_audio_video is a legal input-condition case, expected_error_type should be none")
    return errors


def build_abnormal_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    errors = validate_abnormal_cases(cases)
    counts = Counter(str(case.get("abnormal_type") or "unknown") for case in cases)
    return {
        "status": "failed" if errors else "ready",
        "case_count": len(cases),
        "abnormal_type_counts": dict(sorted(counts.items())),
        "errors": errors,
        "evaluation_policy": "abnormal cases verify system behavior only and never enter clipping-effect scoring",
    }


def write_abnormal_report(summary: dict[str, Any], cases: list[dict[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "abnormal_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# ClawCut 异常场景评测清单",
        "",
        f"- status: {summary['status']}",
        f"- case_count: {summary['case_count']}",
        "",
        "| case_id | abnormal_type | expected_error_type | expected_behavior |",
        "| --- | --- | --- | --- |",
    ]
    for case in cases:
        lines.append(
            f"| {case.get('case_id')} | {case.get('abnormal_type')} | "
            f"{case.get('expected_error_type')} | {case.get('expected_behavior')} |"
        )
    if summary["errors"]:
        lines.extend(["", "## Errors", *(f"- {error}" for error in summary["errors"])])
    (output_dir / "abnormal_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and summarize ClawCut abnormal scenario cases.")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    cases = read_jsonl(args.cases)
    summary = build_abnormal_summary(cases)
    write_abnormal_report(summary, cases, args.output_dir)
    print(f"异常场景报告已生成：{args.output_dir}")
    return 0 if summary["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
