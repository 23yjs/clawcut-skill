from __future__ import annotations

import argparse
import csv
import html
import json
from collections import Counter
from pathlib import Path
from typing import Any


CONCLUSION_LABELS = {
    "excellent": "优秀",
    "usable": "基本可用",
    "needs_work": "需要优化",
    "failed": "执行失败",
    "diagnostic": "仅诊断",
}


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _case_key(row: dict[str, Any]) -> str:
    return str(row.get("case_id") or row.get("video_id") or "case")


def _score_value(row: dict[str, Any]) -> float | None:
    return _to_float(row.get("final_score_v2")) or _to_float(row.get("selection_score_v1"))


def classify_case(row: dict[str, Any]) -> str:
    status = str(row.get("evaluation_status") or row.get("collection_status") or "")
    scope = str(row.get("evaluation_scope") or "")
    fallback = _truthy(row.get("fallback_used"))
    backend = str(row.get("skill_backend_used") or "")
    technical_passed = row.get("technical_quality_passed")
    score = _score_value(row)

    if status in {"failed", "batch_case_failed", "ambiguous_output"} or "failed" in status:
        return "failed"
    if "diagnostic" in status or scope == "diagnostic_only" or fallback or backend == "mock":
        return "diagnostic"
    if str(technical_passed).lower() == "false":
        return "failed"
    if score is None:
        return "needs_work"
    if score >= 85:
        return "excellent"
    if score >= 70:
        return "usable"
    return "needs_work"


def explain_case(row: dict[str, Any]) -> dict[str, str]:
    conclusion = classify_case(row)
    selection_score = _to_float(row.get("selection_score_v1"))
    final_score = _to_float(row.get("final_score_v2"))
    fallback = _truthy(row.get("fallback_used"))
    technical_passed = str(row.get("technical_quality_passed")).lower()
    token_total = row.get("skill_llm_total_tokens") or row.get("resolver_total_tokens") or ""
    latency = row.get("elapsed_seconds") or row.get("skill_llm_latency_seconds") or ""

    if selection_score is None:
        selection_text = "未产生正式选段分，建议先检查执行状态和 Resolver 输出。"
    elif selection_score >= 85:
        selection_text = "核心内容选择表现较好，自动指标显示主要目标覆盖充分。"
    elif selection_score >= 70:
        selection_text = "核心内容选择基本可用，但仍建议抽查是否存在遗漏或冗余。"
    else:
        selection_text = "核心内容覆盖或精简程度不足，建议人工检查是否存在遗漏、冗余或误选。"

    if technical_passed == "true":
        technical_text = "通过，未检测到明显黑屏、冻结、解码或持续静音等硬性问题。"
    elif technical_passed == "false":
        technical_text = "未通过，成片存在技术质量问题，需查看技术附录定位原因。"
    else:
        technical_text = "未完成技术质量检查或结果不可用。"

    if fallback:
        chain_text = "发生 fallback，本次结果只能作为诊断样本，不应混入正式效果评分。"
    else:
        chain_text = "未检测到 Skill fallback；如 OpenClaw transport 为 gateway，可视为正式调用链路结果。"

    cost_text = "暂无 token/耗时字段，可能是历史结果。" if not token_total and not latency else (
        f"耗时 {latency or '未知'} 秒，Skill LLM token {token_total or '未知'}。"
    )

    if conclusion == "failed":
        suggestion = "优先修复执行或技术质量问题，再进入剪辑效果评价。"
    elif conclusion == "diagnostic":
        suggestion = "保留为链路诊断样本；正式统计中应单独计数。"
    elif conclusion == "needs_work":
        suggestion = "建议人工复看片段选择，重点检查低分原因和用户指令遵循。"
    else:
        suggestion = "可作为代表样本保留，并纳入版本回归对比。"

    return {
        "conclusion": CONCLUSION_LABELS[conclusion],
        "selection": selection_text,
        "technical": technical_text,
        "chain": chain_text,
        "cost": cost_text,
        "suggestion": suggestion,
        "score": "" if final_score is None else str(final_score),
    }


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = Counter(classify_case(row) for row in rows)
    scores = [value for value in (_score_value(row) for row in rows) if value is not None]
    return {
        "case_count": len(rows),
        "conclusion_counts": {CONCLUSION_LABELS[key]: labels.get(key, 0) for key in CONCLUSION_LABELS},
        "average_score": round(sum(scores) / len(scores), 3) if scores else None,
        "fallback_count": sum(1 for row in rows if _truthy(row.get("fallback_used"))),
        "failed_count": labels.get("failed", 0),
        "by_test_type": build_breakdown(rows, "test_type"),
        "by_priority": build_breakdown(rows, "priority"),
        "case_studies": build_case_studies(rows),
    }


def build_breakdown(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        group = str(row.get(field) or "未标注")
        grouped.setdefault(group, []).append(row)

    breakdown = []
    for group, group_rows in sorted(grouped.items()):
        scores = [value for value in (_score_value(row) for row in group_rows) if value is not None]
        labels = Counter(classify_case(row) for row in group_rows)
        breakdown.append(
            {
                "name": group,
                "case_count": len(group_rows),
                "average_score": round(sum(scores) / len(scores), 3) if scores else None,
                "conclusion_counts": {CONCLUSION_LABELS[key]: labels.get(key, 0) for key in CONCLUSION_LABELS},
            }
        )
    return breakdown


def _case_study(row: dict[str, Any], reason: str) -> dict[str, Any]:
    explanation = explain_case(row)
    return {
        "case_id": _case_key(row),
        "video_id": row.get("video_id") or "",
        "test_type": row.get("test_type") or "未标注",
        "priority": row.get("priority") or "未标注",
        "conclusion": explanation["conclusion"],
        "score": _score_value(row),
        "why": reason,
        "suggestion": explanation["suggestion"],
    }


def _failure_reason(row: dict[str, Any]) -> str:
    explanation = explain_case(row)
    error = row.get("error_message") or row.get("evaluation_error") or row.get("openclaw_fallback_reason")
    if error:
        return f"执行或产物检查失败：{error}"
    if str(row.get("technical_quality_passed")).lower() == "false":
        return explanation["technical"]
    return explanation["selection"]


def build_case_studies(rows: list[dict[str, Any]], limit: int = 3) -> dict[str, list[dict[str, Any]]]:
    success_candidates = [row for row in rows if classify_case(row) in {"excellent", "usable"}]
    failure_candidates = [row for row in rows if classify_case(row) in {"failed", "needs_work"}]
    diagnostic_candidates = [row for row in rows if classify_case(row) == "diagnostic"]

    success_candidates.sort(key=lambda row: (_score_value(row) is not None, _score_value(row) or -1), reverse=True)
    failure_candidates.sort(
        key=lambda row: (
            classify_case(row) == "failed",
            _score_value(row) is None,
            -(_score_value(row) or 0),
        ),
        reverse=True,
    )
    diagnostic_candidates.sort(key=_case_key)

    return {
        "representative_successes": [
            _case_study(row, explain_case(row)["selection"]) for row in success_candidates[:limit]
        ],
        "representative_failures": [
            _case_study(row, _failure_reason(row)) for row in failure_candidates[:limit]
        ],
        "diagnostic_samples": [
            _case_study(row, explain_case(row)["chain"]) for row in diagnostic_candidates[:limit]
        ],
    }


def _case_html(row: dict[str, Any]) -> str:
    explanation = explain_case(row)
    items = [
        ("视频", row.get("video_id") or row.get("case_id") or ""),
        ("用户要求", row.get("instruction") or ""),
        ("总体结论", explanation["conclusion"]),
        ("内容选择", explanation["selection"]),
        ("技术质量", explanation["technical"]),
        ("调用链路", explanation["chain"]),
        ("耗时与成本", explanation["cost"]),
        ("建议", explanation["suggestion"]),
    ]
    body = "\n".join(
        f"<dt>{html.escape(label)}</dt><dd>{html.escape(str(value))}</dd>"
        for label, value in items
    )
    return f"<section><h2>{html.escape(str(row.get('case_id') or 'case'))}</h2><dl>{body}</dl></section>"


def _breakdown_html(title: str, breakdown: list[dict[str, Any]]) -> str:
    if not breakdown:
        return ""
    rows = []
    for item in breakdown:
        counts = "；".join(f"{key} {value}" for key, value in item["conclusion_counts"].items() if value)
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item['name']))}</td>"
            f"<td>{html.escape(str(item['case_count']))}</td>"
            f"<td>{html.escape(str(item['average_score']))}</td>"
            f"<td>{html.escape(counts or '暂无结论')}</td>"
            "</tr>"
        )
    return (
        f"<section><h2>{html.escape(title)}</h2><table>"
        "<thead><tr><th>分组</th><th>case 数</th><th>平均分</th><th>结论分布</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def _case_studies_html(studies: dict[str, list[dict[str, Any]]]) -> str:
    titles = {
        "representative_successes": "典型成功案例",
        "representative_failures": "典型失败或待优化案例",
        "diagnostic_samples": "诊断样本",
    }
    sections = []
    for key, title in titles.items():
        items = studies.get(key) or []
        if not items:
            sections.append(f"<section><h2>{html.escape(title)}</h2><p>暂无。</p></section>")
            continue
        lines = []
        for item in items:
            score = "暂无" if item.get("score") is None else str(item.get("score"))
            lines.append(
                "<li>"
                f"<strong>{html.escape(str(item['case_id']))}</strong>"
                f"（{html.escape(str(item['conclusion']))}，分数 {html.escape(score)}）"
                f"<br>为什么：{html.escape(str(item['why']))}"
                f"<br>建议：{html.escape(str(item['suggestion']))}"
                "</li>"
            )
        sections.append(f"<section><h2>{html.escape(title)}</h2><ul>{''.join(lines)}</ul></section>")
    return "".join(sections)


def write_reports(*, rows: list[dict[str, Any]], output_dir: Path, source_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cases_dir = output_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary(rows)
    summary_payload = dict(source_summary or {})
    if summary_payload:
        summary_payload["human_report"] = summary
    else:
        summary_payload = summary

    for row in rows:
        case_id = str(row.get("case_id") or row.get("video_id") or "case")
        (cases_dir / f"{case_id}.html").write_text(
            "<!doctype html><meta charset='utf-8'>\n" + _case_html(row),
            encoding="utf-8",
        )

    main_sections = "\n".join(_case_html(row) for row in rows)
    breakdown_sections = (
        _breakdown_html("按测试类型汇总", summary["by_test_type"])
        + _breakdown_html("按优先级汇总", summary["by_priority"])
    )
    case_study_sections = _case_studies_html(summary["case_studies"])
    summary_items = "".join(
        f"<li>{html.escape(str(key))}: {html.escape(str(value))}</li>"
        for key, value in summary.items()
        if key not in {"source_summary", "by_test_type", "by_priority", "case_studies"}
    )
    (output_dir / "report.html").write_text(
        "<!doctype html><meta charset='utf-8'>"
        "<title>ClawCut 评测报告</title>"
        "<h1>ClawCut 评测报告</h1>"
        f"<ul>{summary_items}</ul>"
        f"{breakdown_sections}"
        f"{case_study_sections}"
        f"{main_sections}",
        encoding="utf-8",
    )
    (output_dir / "technical_appendix.html").write_text(
        "<!doctype html><meta charset='utf-8'><h1>技术附录</h1><pre>"
        + html.escape(json.dumps(rows, ensure_ascii=False, indent=2))
        + "</pre>",
        encoding="utf-8",
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_lines = [
        "# ClawCut 评测摘要",
        "",
        f"- 总 case 数：{summary['case_count']}",
        f"- 平均分：{summary['average_score']}",
        f"- fallback 数：{summary['fallback_count']}",
        f"- 失败数：{summary['failed_count']}",
        "",
        "## 能力维度汇总",
    ]
    for item in summary["by_test_type"]:
        md_lines.append(
            f"- {item['name']}：{item['case_count']} 条，平均分 {item['average_score']}，结论分布 {item['conclusion_counts']}"
        )
    md_lines.extend(
        [
            "",
            "## 典型成功案例",
        ]
    )
    for item in summary["case_studies"]["representative_successes"]:
        md_lines.append(f"- {item['case_id']}：{item['why']} 建议：{item['suggestion']}")
    if not summary["case_studies"]["representative_successes"]:
        md_lines.append("- 暂无。")
    md_lines.extend(
        [
            "",
            "## 典型失败或待优化案例",
        ]
    )
    for item in summary["case_studies"]["representative_failures"]:
        md_lines.append(f"- {item['case_id']}：{item['why']} 建议：{item['suggestion']}")
    if not summary["case_studies"]["representative_failures"]:
        md_lines.append("- 暂无。")
    md_lines.extend(
        [
            "",
            "## 诊断样本",
        ]
    )
    for item in summary["case_studies"]["diagnostic_samples"]:
        md_lines.append(f"- {item['case_id']}：{item['why']}")
    if not summary["case_studies"]["diagnostic_samples"]:
        md_lines.append("- 暂无。")
    md_lines.extend(
        [
            "",
            "## 单条结论",
        ]
    )
    for row in rows:
        explanation = explain_case(row)
        md_lines.append(f"- {row.get('case_id')}: {explanation['conclusion']}；{explanation['selection']}")
    (output_dir / "summary.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return summary_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate human-readable ClawCut evaluation reports.")
    parser.add_argument("--results-csv", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    rows = _read_csv(args.results_csv)
    source_summary = _read_json(args.summary_json) if args.summary_json else {}
    write_reports(rows=rows, output_dir=args.output_dir, source_summary=source_summary)
    print(f"报告已生成：{args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
