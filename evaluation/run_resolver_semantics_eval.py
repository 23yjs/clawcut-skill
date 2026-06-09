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
COMPARISON_POLICY = {
    "segment_id_lists": "ordered_exact_match",
    "duration_constraint": "expected_keys_exact_match",
}
EXPECTED_FIELD_ALIASES = {
    "expected_relevant_segment_ids": "relevant_segment_ids",
    "expected_forbidden_segment_ids": "forbidden_segment_ids",
    "expected_duration_constraint": "duration_constraint",
}
MODE_LABELS = {
    "generic": "默认高光",
    "specific": "指定内容",
    "conflict": "指定内容并排除明确禁止项",
    "unresolved": "无法解析",
}
SCOPE_LABELS = {
    "not_applicable": "不适用",
    "preferential": "优先保留",
    "exclusive": "严格只保留",
    "unknown": "未知",
}
STATUS_LABELS = {
    "resolved": "已解析",
    "partial": "部分解析",
    "unresolved": "未解析",
}


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


def _expected_fields(case: dict[str, Any]) -> list[tuple[str, Any]]:
    fields: list[tuple[str, Any]] = []
    for key, expected in case.items():
        if not key.startswith("expected_"):
            continue
        actual_key = EXPECTED_FIELD_ALIASES.get(key, key.removeprefix("expected_"))
        fields.append((actual_key, expected))
    return fields


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _compare_expected_field(field: str, expected: Any, actual: dict[str, Any]) -> str | None:
    if field not in actual:
        return f"{field}: expected {_json_text(expected)}, got <missing>"
    actual_value = actual.get(field)
    if field in {"relevant_segment_ids", "forbidden_segment_ids"}:
        if not isinstance(actual_value, list):
            return f"{field}: expected list {_json_text(expected)}, got {_json_text(actual_value)}"
        if list(actual_value) != list(expected):
            return (
                f"{field}: expected ordered list {_json_text(expected)}, "
                f"got {_json_text(actual_value)}"
            )
        return None
    if field == "duration_constraint" and isinstance(expected, dict):
        if not isinstance(actual_value, dict):
            return f"{field}: expected object {_json_text(expected)}, got {_json_text(actual_value)}"
        mismatched_keys = []
        for key, expected_value in expected.items():
            if key not in actual_value:
                mismatched_keys.append(f"{key}: expected {_json_text(expected_value)}, got <missing>")
            elif actual_value.get(key) != expected_value:
                mismatched_keys.append(
                    f"{key}: expected {_json_text(expected_value)}, got {_json_text(actual_value.get(key))}"
                )
        if mismatched_keys:
            return f"{field}: " + "; ".join(mismatched_keys)
        return None
    if actual_value != expected:
        return f"{field}: expected {_json_text(expected)}, got {_json_text(actual_value)}"
    return None


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
        expected = {}
        for field, expected_value in _expected_fields(case):
            expected[field] = expected_value
            mismatch = _compare_expected_field(field, expected_value, actual)
            if mismatch:
                mismatches.append(mismatch)
        passed = not mismatches
        return {
            "case_id": case_id,
            "video_id": case.get("video_id"),
            "instruction": case.get("instruction"),
            "why_this_case": case.get("why_this_case"),
            "passed": passed,
            "status": "passed" if passed else "failed",
            "mismatches": mismatches,
            "expected": expected,
            "actual": actual,
            "metadata": metadata,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "case_id": case_id,
            "video_id": case.get("video_id"),
            "instruction": case.get("instruction"),
            "why_this_case": case.get("why_this_case"),
            "passed": False,
            "status": "failed",
            "mismatches": [f"{exc.__class__.__name__}: {exc}"],
            "expected": {field: expected for field, expected in _expected_fields(case)},
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
        "comparison_policy": COMPARISON_POLICY,
        "result_rows": rows,
    }


def _label(mapping: dict[str, str], value: Any) -> str:
    text = str(value or "")
    return mapping.get(text, text or "—")


def _fmt_json(value: Any) -> str:
    if value in (None, "", []):
        return "—" if value in (None, "") else "[]"
    return json.dumps(value, ensure_ascii=False, indent=2)


def _kv_table(title: str, payload: dict[str, Any]) -> str:
    rows = []
    for key, value in payload.items():
        rows.append(
            "<tr>"
            f"<th>{html.escape(str(key))}</th>"
            f"<td><pre>{html.escape(_fmt_json(value))}</pre></td>"
            "</tr>"
        )
    return f"<h3>{html.escape(title)}</h3><table class=\"kv\"><tbody>{''.join(rows)}</tbody></table>"


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
        "## 比较策略",
        "",
        "- segment_id 列表：顺序完全一致才通过。",
        "- duration_constraint：只比较 expected_duration_constraint 中列出的 key，逐项精确匹配。",
        "- actual 缺少 expected 字段：直接判定 mismatch。",
        "",
        "| case_id | status | mismatches |",
        "| --- | --- | --- |",
    ]
    for row in summary["result_rows"]:
        lines.append(f"| {row['case_id']} | {row['status']} | {'; '.join(row.get('mismatches') or [])} |")
    (output_dir / "resolver_semantics_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    blocks = []
    for row in summary["result_rows"]:
        actual = row.get("actual") if isinstance(row.get("actual"), dict) else {}
        expected = row.get("expected") if isinstance(row.get("expected"), dict) else {}
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        status_class = "ok" if row.get("passed") else "bad"
        core_rows = [
            ("用户指令", row.get("instruction")),
            ("设计目的", row.get("why_this_case")),
            ("模式", _label(MODE_LABELS, actual.get("instruction_mode"))),
            ("内容筛选范围", _label(SCOPE_LABELS, actual.get("selection_scope"))),
            ("解析状态", _label(STATUS_LABELS, actual.get("resolution_status"))),
            ("模型", metadata.get("resolver_model")),
            ("Token", (metadata.get("resolver_usage") or {}).get("total_tokens") if isinstance(metadata.get("resolver_usage"), dict) else None),
            ("耗时", metadata.get("resolver_latency_seconds")),
            ("是否通过", "通过" if row.get("passed") else "未通过"),
            ("失败原因", "；".join(row.get("mismatches") or []) or "—"),
        ]
        kv = "".join(
            "<tr>"
            f"<th>{html.escape(label)}</th>"
            f"<td>{html.escape(str(value if value not in (None, '') else '—'))}</td>"
            "</tr>"
            for label, value in core_rows
        )
        actual_subset = {
            "instruction_mode": actual.get("instruction_mode"),
            "selection_scope": actual.get("selection_scope"),
            "resolution_status": actual.get("resolution_status"),
            "relevant_segment_ids": actual.get("relevant_segment_ids"),
            "forbidden_segment_ids": actual.get("forbidden_segment_ids"),
            "duration_constraint": actual.get("duration_constraint"),
            "resolver_reason": actual.get("resolver_reason"),
        }
        blocks.append(
            f"<section class=\"card\"><h2>{html.escape(str(row.get('case_id') or '—'))}"
            f" <span class=\"badge {status_class}\">{'通过' if row.get('passed') else '未通过'}</span></h2>"
            f"<table class=\"kv\"><tbody>{kv}</tbody></table>"
            f"{_kv_table('预期值', expected)}"
            f"{_kv_table('实际值', actual_subset)}"
            "</section>"
        )
    html_doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>Resolver 语义解析专项</title><style>
body{{margin:0;background:#f5f7fb;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif}}main{{max-width:1200px;margin:0 auto;padding:32px 28px}}h1,h2,h3{{color:#12365f}}.card{{background:#fff;border:1px solid #d9e1ec;border-radius:14px;padding:20px;margin-top:18px}}table{{width:100%;border-collapse:collapse;margin-top:10px}}th,td{{border:1px solid #d9e1ec;padding:10px;text-align:left;vertical-align:top}}th{{background:#f0f4f9;width:220px}}pre{{white-space:pre-wrap;margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}a{{color:#175cd3;text-decoration:none}}.badge{{font-size:13px;border-radius:999px;padding:4px 9px;margin-left:8px}}.ok{{background:#dcfce7;color:#166534}}.bad{{background:#fee2e2;color:#991b1b}}.policy{{line-height:1.7}}</style></head><body><main>
<h1>Resolver 语义解析专项</h1>
<section class="card"><p>通过 {summary.get('passed_count', 0)} / {summary.get('case_count', 0)}；失败 {summary.get('failed_count', 0)}；未执行 {summary.get('not_run_count', 0)}</p></section>
<section class="card policy"><h2>比较策略</h2><p>segment_id 列表采用顺序完全一致比较；duration_constraint 只比较预期中声明的 key；actual 缺少 expected 字段时直接判定为失败。</p></section>
{''.join(blocks)}
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
