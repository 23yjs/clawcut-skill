from __future__ import annotations

import argparse
import html
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


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _result_by_case_id(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for row in results:
        case_id = str(row.get("case_id") or "").strip()
        if case_id:
            mapped[case_id] = row
    return mapped


def _result_path_exists(row: dict[str, Any], bool_key: str, path_key: str) -> bool:
    if bool_key in row:
        return _truthy(row.get(bool_key))
    path = row.get(path_key)
    return bool(path and Path(str(path)).exists())


def _evaluate_abnormal_result(case: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "")
    expected_error_type = str(case.get("expected_error_type") or "")
    expected_is_legal_input = expected_error_type == "none"
    reasons: list[str] = []
    if result is None:
        return {
            "case_id": case_id,
            "abnormal_type": case.get("abnormal_type"),
            "verification_mode": str(case.get("verification_mode") or ""),
            "expected_error_type": expected_error_type,
            "actual_error_type": "",
            "actual_status": "not_run",
            "result_summary_exists": False,
            "run_log_exists": False,
            "highlight_video_exists": False,
            "timed_out": False,
            "entered_official_scoring": False,
            "passed": False,
            "reasons": "no result row",
        }

    actual_error_type = str(result.get("actual_error_type") or result.get("error_type") or "")
    actual_status = str(result.get("status") or result.get("run_status") or result.get("collection_status") or "")
    result_summary_exists = _result_path_exists(result, "result_summary_exists", "result_summary")
    run_log_exists = _result_path_exists(result, "run_log_exists", "run_log")
    highlight_video_exists = _result_path_exists(result, "highlight_video_exists", "highlight_video")
    timed_out = _truthy(result.get("timed_out")) or actual_status in {"timeout", "hung", "stalled"}
    entered_official_scoring = _truthy(result.get("entered_official_scoring"))

    if timed_out:
        reasons.append("execution timed out or hung")
    if entered_official_scoring:
        reasons.append("abnormal result entered official scoring")
    if not result_summary_exists:
        reasons.append("missing result_summary")
    if not run_log_exists:
        reasons.append("missing run_log")

    if expected_is_legal_input:
        if actual_error_type not in {"", "none"}:
            reasons.append(f"unexpected error_type {actual_error_type}")
        if actual_status in {"failed", "batch_case_failed", "timeout", "hung"} or "failed" in actual_status:
            reasons.append(f"legal input failed with status {actual_status}")
    else:
        if actual_error_type != expected_error_type:
            reasons.append(f"expected error_type {expected_error_type}, got {actual_error_type or 'empty'}")
        if highlight_video_exists:
            reasons.append("misleading highlight generated for abnormal failure")

    return {
        "case_id": case_id,
        "abnormal_type": case.get("abnormal_type"),
        "verification_mode": str(result.get("verification_mode") or case.get("verification_mode") or ""),
        "expected_error_type": expected_error_type,
        "actual_error_type": actual_error_type,
        "actual_status": actual_status,
        "result_summary_exists": result_summary_exists,
        "run_log_exists": run_log_exists,
        "highlight_video_exists": highlight_video_exists,
        "timed_out": timed_out,
        "entered_official_scoring": entered_official_scoring,
        "passed": not reasons,
        "reasons": "; ".join(reasons),
    }


def _verification_label(value: Any) -> str:
    text = str(value or "")
    if text in {"real_input", "real_input_mock_backend"}:
        return "真实异常输入" if text == "real_input" else "真实媒体链路"
    if text == "fault_injection":
        return "定向故障注入"
    return text or "—"


def _write_detail_html(summary: dict[str, Any], cases: list[dict[str, Any]], output_dir: Path) -> None:
    cases_by_id = {str(case.get("case_id")): case for case in cases}
    rows = []
    for result in summary.get("result_rows", []):
        case = cases_by_id.get(str(result.get("case_id")), {})
        expected = case.get("expected_behavior") or case.get("expected_error_type") or "—"
        actual = (
            f"状态：{result.get('actual_status') or '—'}；"
            f"错误类型：{result.get('actual_error_type') or '—'}；"
            f"成片：{'存在' if result.get('highlight_video_exists') else '不存在'}"
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(result.get('abnormal_type') or '—'))}</td>"
            f"<td>{html.escape(_verification_label(result.get('verification_mode') or case.get('verification_mode')))}</td>"
            f"<td>{html.escape(str(expected))}</td>"
            f"<td>{html.escape(actual)}</td>"
            f"<td>{'通过' if result.get('passed') else '未通过'}</td>"
            "</tr>"
        )
    html_doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>异常输入处理专项</title><style>
body{{margin:0;background:#f5f7fb;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif}}main{{max-width:1200px;margin:0 auto;padding:32px 28px}}h1{{color:#12365f}}.card{{background:#fff;border:1px solid #d9e1ec;border-radius:14px;padding:20px;margin-top:18px}}table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{border:1px solid #d9e1ec;padding:10px;vertical-align:top;text-align:left}}th{{background:#f0f4f9}}.ok{{color:#18864b;font-weight:700}}a{{color:#175cd3;text-decoration:none}}</style></head><body><main>
<h1>异常输入处理专项</h1>
<section class="card"><p>通过 <strong class="ok">{summary.get('passed_result_count', 0)} / {summary.get('result_count', 0)}</strong>；失败 {summary.get('failed_result_count', 0)}；未执行 {summary.get('not_run_count', 0)}</p><p>{html.escape(str(summary.get('evaluation_policy') or ''))}</p></section>
<section class="card"><table><thead><tr><th>异常类型</th><th>验证方式</th><th>预期结果</th><th>实际结果</th><th>是否通过</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
<p><a href="../../report.html">返回总览报告</a></p>
</main></body></html>"""
    (output_dir / "detail.html").write_text(html_doc, encoding="utf-8")


def build_abnormal_summary(cases: list[dict[str, Any]], results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    errors = validate_abnormal_cases(cases)
    counts = Counter(str(case.get("abnormal_type") or "unknown") for case in cases)
    result_rows: list[dict[str, Any]] = []
    if results is not None:
        mapped_results = _result_by_case_id(results)
        result_rows = [_evaluate_abnormal_result(case, mapped_results.get(str(case.get("case_id")))) for case in cases]
    failed_count = sum(1 for row in result_rows if not row["passed"])
    not_run_count = sum(1 for row in result_rows if row["actual_status"] == "not_run")
    return {
        "status": "failed" if errors or failed_count else "ready",
        "case_count": len(cases),
        "abnormal_type_counts": dict(sorted(counts.items())),
        "errors": errors,
        "result_count": len(results or []),
        "passed_result_count": sum(1 for row in result_rows if row["passed"]),
        "failed_result_count": failed_count,
        "not_run_count": not_run_count,
        "result_rows": result_rows,
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
        f"- result_count: {summary['result_count']}",
        f"- passed_result_count: {summary['passed_result_count']}",
        f"- failed_result_count: {summary['failed_result_count']}",
        f"- not_run_count: {summary['not_run_count']}",
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
    if summary["result_rows"]:
        lines.extend(
            [
                "",
                "## Actual Result Checks",
                "| case_id | expected_error_type | actual_error_type | status | passed | reasons |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in summary["result_rows"]:
            lines.append(
                f"| {row['case_id']} | {row['expected_error_type']} | {row['actual_error_type']} | "
                f"{row['actual_status']} | {row['passed']} | {row['reasons']} |"
            )
    (output_dir / "abnormal_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_detail_html(summary, cases, output_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and summarize ClawCut abnormal scenario cases.")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--results-jsonl", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    cases = read_jsonl(args.cases)
    results = read_jsonl(args.results_jsonl) if args.results_jsonl else None
    summary = build_abnormal_summary(cases, results)
    write_abnormal_report(summary, cases, args.output_dir)
    print(f"异常场景报告已生成：{args.output_dir}")
    return 0 if summary["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
