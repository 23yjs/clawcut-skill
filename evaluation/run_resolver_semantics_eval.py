from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

try:
    from .ark_resolver_client import ArkResolverConfig
    from .instruction_resolver import resolve_instruction_with_ark
except ImportError:  # pragma: no cover - script mode
    from ark_resolver_client import ArkResolverConfig
    from instruction_resolver import resolve_instruction_with_ark


DEFAULT_CASES = Path("data/eval/resolver_semantics_cases.v1.with_llm_video_url.jsonl")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{line_number}: JSONL 行必须是 object")
            rows.append(payload)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _expected_fields(case: dict[str, Any], actual: dict[str, Any]) -> list[tuple[str, Any]]:
    fields: list[tuple[str, Any]] = []
    for key, expected in case.items():
        if not key.startswith("expected_"):
            continue
        actual_key = key.removeprefix("expected_")
        if actual_key in actual:
            fields.append((actual_key, expected))
    return fields


def evaluate_case(case: dict[str, Any], gt_dir: Path, config: ArkResolverConfig) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "")
    gt_path = gt_dir / f"{case['video_id']}.json"
    try:
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        actual, metadata = resolve_instruction_with_ark(
            instruction=str(case["instruction"]),
            target_duration=case.get("target_duration"),
            gt_annotation=gt,
            config=config,
        )
        mismatches = []
        for field, expected in _expected_fields(case, actual):
            actual_value = actual.get(field)
            if actual_value != expected:
                mismatches.append(f"{field}: expected {expected!r}, got {actual_value!r}")
        passed = not mismatches
        return {
            "case_id": case_id,
            "video_id": case.get("video_id"),
            "instruction": case.get("instruction"),
            "passed": passed,
            "status": "passed" if passed else "failed",
            "mismatches": mismatches,
            "actual": actual,
            "metadata": metadata,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "case_id": case_id,
            "video_id": case.get("video_id"),
            "instruction": case.get("instruction"),
            "passed": False,
            "status": "failed",
            "mismatches": [f"{exc.__class__.__name__}: {exc}"],
            "actual": {},
            "metadata": {},
        }


def build_summary(cases: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [row["case_id"] for row in rows if not row.get("passed")]
    return {
        "status": "ready" if not failed else "failed",
        "case_count": len(cases),
        "result_count": len(rows),
        "passed_count": len(rows) - len(failed),
        "failed_count": len(failed),
        "not_run_count": max(0, len(cases) - len(rows)),
        "failed_case_ids": failed,
        "result_rows": rows,
    }


def write_report(summary: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "resolver_semantics_summary.json", summary)
    lines = [
        "# Resolver 语义解析专项",
        "",
        f"- case_count: {summary['case_count']}",
        f"- passed_count: {summary['passed_count']}",
        f"- failed_count: {summary['failed_count']}",
        f"- not_run_count: {summary['not_run_count']}",
        "",
        "| case_id | status | mismatches |",
        "| --- | --- | --- |",
    ]
    for row in summary["result_rows"]:
        lines.append(f"| {row['case_id']} | {row['status']} | {'; '.join(row.get('mismatches') or [])} |")
    (output_dir / "resolver_semantics_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    trs = []
    for row in summary["result_rows"]:
        actual = row.get("actual") if isinstance(row.get("actual"), dict) else {}
        trs.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('case_id') or '—'))}</td>"
            f"<td>{html.escape(str(row.get('instruction') or '—'))}</td>"
            f"<td>{html.escape(str(actual.get('instruction_mode') or '—'))}</td>"
            f"<td>{html.escape(str(actual.get('selection_scope') or '—'))}</td>"
            f"<td>{html.escape(str(actual.get('resolution_status') or '—'))}</td>"
            f"<td>{'通过' if row.get('passed') else '未通过'}</td>"
            f"<td>{html.escape('; '.join(row.get('mismatches') or []) or '—')}</td>"
            "</tr>"
        )
    html_doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>Resolver 语义解析专项</title><style>
body{{margin:0;background:#f5f7fb;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif}}main{{max-width:1200px;margin:0 auto;padding:32px 28px}}h1{{color:#12365f}}.card{{background:#fff;border:1px solid #d9e1ec;border-radius:14px;padding:20px;margin-top:18px}}table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid #d9e1ec;padding:10px;text-align:left;vertical-align:top}}th{{background:#f0f4f9}}a{{color:#175cd3;text-decoration:none}}</style></head><body><main>
<h1>Resolver 语义解析专项</h1>
<section class="card"><p>通过 {summary.get('passed_count', 0)} / {summary.get('case_count', 0)}；失败 {summary.get('failed_count', 0)}；未执行 {summary.get('not_run_count', 0)}</p></section>
<section class="card"><table><thead><tr><th>Case ID</th><th>用户指令</th><th>模式</th><th>范围</th><th>解析状态</th><th>是否通过</th><th>失败原因</th></tr></thead><tbody>{''.join(trs)}</tbody></table></section>
<p><a href="../../report.html">返回总览报告</a></p>
</main></body></html>"""
    (output_dir / "detail.html").write_text(html_doc, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Resolver semantic contract evaluation.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--gt-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default=ArkResolverConfig().model)
    parser.add_argument("--timeout-seconds", type=int, default=ArkResolverConfig().timeout_seconds)
    args = parser.parse_args(argv)
    cases = read_jsonl(args.cases)
    config = ArkResolverConfig(model=args.model, timeout_seconds=args.timeout_seconds)
    rows = [evaluate_case(case, args.gt_dir, config) for case in cases]
    summary = build_summary(cases, rows)
    write_report(summary, args.output_dir)
    print(f"Resolver 语义解析专项报告已生成：{args.output_dir}")
    return 0 if summary["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
