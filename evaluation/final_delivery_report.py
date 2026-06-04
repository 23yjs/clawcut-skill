from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_INPUTS = {
    "official_summary": Path("eval_outputs/official_v1/summary.json"),
    "readiness": Path("eval_outputs/official_v1_readiness/official_case_readiness.json"),
    "abnormal": Path("eval_outputs/abnormal_v1/abnormal_summary.json"),
    "stability": Path("eval_outputs/stability_v1/stability_summary.json"),
    "fps": Path("eval_outputs/fps_sensitivity_v1/fps_sensitivity_summary.json"),
    "regression": Path("eval_outputs/regression_v1/regression_summary.json"),
}


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def _human_report(summary: dict[str, Any] | None) -> dict[str, Any]:
    if not summary:
        return {}
    payload = summary.get("human_report")
    return payload if isinstance(payload, dict) else summary


def _official_section(summary: dict[str, Any] | None) -> dict[str, Any]:
    human = _human_report(summary)
    return {
        "available": bool(summary),
        "case_count": human.get("case_count"),
        "average_score": human.get("average_score"),
        "conclusion_counts": human.get("conclusion_counts") or {},
        "fallback_count": human.get("fallback_count"),
        "failed_count": human.get("failed_count"),
        "by_test_type": human.get("by_test_type") or [],
        "case_studies": human.get("case_studies") or {},
    }


def _readiness_section(summary: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "available": bool(summary),
        "case_count": (summary or {}).get("case_count"),
        "ready_for_official_eval": (summary or {}).get("ready_for_official_eval"),
        "not_ready_count": (summary or {}).get("not_ready_count"),
        "status_counts": (summary or {}).get("status_counts") or {},
    }


def _abnormal_section(summary: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "available": bool(summary),
        "status": (summary or {}).get("status"),
        "case_count": (summary or {}).get("case_count"),
        "abnormal_type_counts": (summary or {}).get("abnormal_type_counts") or {},
        "errors": (summary or {}).get("errors") or [],
    }


def _stability_section(summary: dict[str, Any] | None) -> dict[str, Any]:
    overall = (summary or {}).get("overall") or {}
    cases = (summary or {}).get("cases") or []
    slowest = sorted(
        [case for case in cases if case.get("max_latency_seconds") is not None],
        key=lambda case: float(case.get("max_latency_seconds") or 0),
        reverse=True,
    )[:3]
    costly = sorted(
        [case for case in cases if case.get("estimated_cost") is not None],
        key=lambda case: float(case.get("estimated_cost") or 0),
        reverse=True,
    )[:3]
    return {
        "available": bool(summary),
        "case_count": (summary or {}).get("case_count"),
        "attempt_count": (summary or {}).get("attempt_count"),
        "official_success_rate": overall.get("official_success_rate"),
        "skill_fallback_rate": overall.get("skill_fallback_rate"),
        "openclaw_fallback_rate": overall.get("openclaw_fallback_rate"),
        "estimated_cost": overall.get("estimated_cost"),
        "slowest_cases": slowest,
        "costliest_cases": costly,
    }


def _fps_section(summary: dict[str, Any] | None) -> dict[str, Any]:
    recommendations = (summary or {}).get("recommendations") or []
    missed = [
        row
        for row in (summary or {}).get("rows", [])
        if row.get("result_available") and row.get("short_highlight_missed")
    ]
    return {
        "available": bool(summary),
        "case_count": (summary or {}).get("case_count"),
        "result_count": (summary or {}).get("result_count"),
        "missed_count": len(missed),
        "recommendations": recommendations,
    }


def _regression_section(summary: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "available": bool(summary),
        "gate_passed": (summary or {}).get("gate_passed"),
        "average_score_delta": (summary or {}).get("average_score_delta"),
        "failed_case_regressions": (summary or {}).get("failed_case_regressions"),
        "fallback_regressions": (summary or {}).get("fallback_regressions"),
        "technical_quality_regressions": (summary or {}).get("technical_quality_regressions"),
        "gate_failures": (summary or {}).get("gate_failures") or [],
    }


def _overall_conclusion(report: dict[str, Any]) -> str:
    missing = report["missing_sections"]
    official = report["official_effect"]
    abnormal = report["abnormal"]
    stability = report["stability_cost"]
    regression = report["regression"]
    readiness = report["readiness"]

    if missing:
        return "证据未闭环"
    if regression.get("gate_passed") is False:
        return "需要优化"
    if abnormal.get("status") == "failed":
        return "需要优化"
    if readiness.get("not_ready_count"):
        return "部分可评测"
    if stability.get("official_success_rate") is not None and float(stability["official_success_rate"]) < 0.8:
        return "需要优化"
    average_score = official.get("average_score")
    if average_score is not None and float(average_score) >= 85:
        return "优秀"
    if average_score is not None and float(average_score) >= 70:
        return "基本可用"
    return "需要优化"


def build_final_delivery_report(inputs: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    missing_sections = sorted(name for name, payload in inputs.items() if payload is None)
    report = {
        "report_version": "final_delivery_report_v1",
        "missing_sections": missing_sections,
        "readiness": _readiness_section(inputs.get("readiness")),
        "official_effect": _official_section(inputs.get("official_summary")),
        "abnormal": _abnormal_section(inputs.get("abnormal")),
        "stability_cost": _stability_section(inputs.get("stability")),
        "fps_sensitivity": _fps_section(inputs.get("fps")),
        "regression": _regression_section(inputs.get("regression")),
    }
    report["overall_conclusion"] = _overall_conclusion(report)
    return report


def _format_counts(counts: dict[str, Any]) -> str:
    return "；".join(f"{key} {value}" for key, value in counts.items()) if counts else "暂无"


def _case_lines(items: list[dict[str, Any]], *, with_suggestion: bool = True) -> list[str]:
    if not items:
        return ["- 暂无。"]
    lines = []
    for item in items:
        suggestion = f" 建议：{item.get('suggestion')}" if with_suggestion and item.get("suggestion") else ""
        lines.append(f"- {item.get('case_id')}：{item.get('why')}{suggestion}")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    official = report["official_effect"]
    readiness = report["readiness"]
    abnormal = report["abnormal"]
    stability = report["stability_cost"]
    fps = report["fps_sensitivity"]
    regression = report["regression"]
    case_studies = official.get("case_studies") or {}

    lines = [
        "# ClawCut 最终评测交付报告",
        "",
        f"- 总体结论：{report['overall_conclusion']}",
        f"- 缺失证据层：{', '.join(report['missing_sections']) if report['missing_sections'] else '无'}",
        "",
        "## 1. 正式效果评测",
        f"- case_count: {official.get('case_count')}",
        f"- average_score: {official.get('average_score')}",
        f"- conclusion_counts: {_format_counts(official.get('conclusion_counts') or {})}",
        f"- fallback_count: {official.get('fallback_count')}",
        f"- failed_count: {official.get('failed_count')}",
        "",
        "### 能力覆盖",
    ]
    for item in official.get("by_test_type") or []:
        lines.append(
            f"- {item.get('name')}：{item.get('case_count')} 条，平均分 {item.get('average_score')}，"
            f"结论分布 {_format_counts(item.get('conclusion_counts') or {})}"
        )
    if not official.get("by_test_type"):
        lines.append("- 暂无。")

    lines.extend(
        [
            "",
            "### 典型成功案例",
            *_case_lines(case_studies.get("representative_successes") or []),
            "",
            "### 典型失败或待优化案例",
            *_case_lines(case_studies.get("representative_failures") or []),
            "",
            "### 诊断样本",
            *_case_lines(case_studies.get("diagnostic_samples") or [], with_suggestion=False),
            "",
            "## 2. 产物就绪情况",
            f"- official case 数：{readiness.get('case_count')}",
            f"- ready_for_official_eval：{readiness.get('ready_for_official_eval')}",
            f"- not_ready_count：{readiness.get('not_ready_count')}",
            f"- status_counts：{_format_counts(readiness.get('status_counts') or {})}",
            "",
            "## 3. 异常场景评测",
            f"- status：{abnormal.get('status')}",
            f"- case_count：{abnormal.get('case_count')}",
            f"- abnormal_type_counts：{_format_counts(abnormal.get('abnormal_type_counts') or {})}",
            f"- errors：{'; '.join(abnormal.get('errors') or []) if abnormal.get('errors') else '无'}",
            "",
            "## 4. 稳定性、性能与成本",
            f"- case_count：{stability.get('case_count')}",
            f"- attempt_count：{stability.get('attempt_count')}",
            f"- official_success_rate：{stability.get('official_success_rate')}",
            f"- skill_fallback_rate：{stability.get('skill_fallback_rate')}",
            f"- openclaw_fallback_rate：{stability.get('openclaw_fallback_rate')}",
            f"- estimated_cost：{stability.get('estimated_cost')}",
            "",
            "### 最慢案例",
        ]
    )
    lines.extend(f"- {case.get('case_id')}：max_latency_seconds={case.get('max_latency_seconds')}" for case in stability.get("slowest_cases") or [])
    if not stability.get("slowest_cases"):
        lines.append("- 暂无。")
    lines.extend(["", "### 成本最高案例"])
    lines.extend(f"- {case.get('case_id')}：estimated_cost={case.get('estimated_cost')}" for case in stability.get("costliest_cases") or [])
    if not stability.get("costliest_cases"):
        lines.append("- 暂无。")

    lines.extend(
        [
            "",
            "## 5. 高动态 FPS 敏感性",
            f"- case_count：{fps.get('case_count')}",
            f"- result_count：{fps.get('result_count')}",
            f"- missed_count：{fps.get('missed_count')}",
            "",
            "### 建议",
        ]
    )
    lines.extend(f"- {item.get('case_id')}：{item.get('recommendation')}" for item in fps.get("recommendations") or [])
    if not fps.get("recommendations"):
        lines.append("- 暂无。")

    lines.extend(
        [
            "",
            "## 6. 版本回归",
            f"- gate_passed：{regression.get('gate_passed')}",
            f"- average_score_delta：{regression.get('average_score_delta')}",
            f"- failed_case_regressions：{regression.get('failed_case_regressions')}",
            f"- fallback_regressions：{regression.get('fallback_regressions')}",
            f"- technical_quality_regressions：{regression.get('technical_quality_regressions')}",
            f"- gate_failures：{'; '.join(regression.get('gate_failures') or []) if regression.get('gate_failures') else '无'}",
            "",
            "## 7. 交付判断",
            "- 如果缺失证据层不为空，本报告只能作为阶段性报告。",
            "- 如果 regression gate 未通过，不能宣称新版本整体变好。",
            "- 如果 readiness 中仍有 missing_artifacts，需要继续补齐 OpenClaw 真实剪辑产物。",
        ]
    )
    return "\n".join(lines) + "\n"


def write_final_delivery_report(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "final_delivery_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "FINAL_EVALUATION_REPORT.md").write_text(render_markdown(report), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble final ClawCut evaluation delivery report.")
    parser.add_argument("--official-summary-json", type=Path, default=DEFAULT_INPUTS["official_summary"])
    parser.add_argument("--readiness-json", type=Path, default=DEFAULT_INPUTS["readiness"])
    parser.add_argument("--abnormal-summary-json", type=Path, default=DEFAULT_INPUTS["abnormal"])
    parser.add_argument("--stability-summary-json", type=Path, default=DEFAULT_INPUTS["stability"])
    parser.add_argument("--fps-summary-json", type=Path, default=DEFAULT_INPUTS["fps"])
    parser.add_argument("--regression-summary-json", type=Path, default=DEFAULT_INPUTS["regression"])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    inputs = {
        "official_summary": _read_json(args.official_summary_json),
        "readiness": _read_json(args.readiness_json),
        "abnormal": _read_json(args.abnormal_summary_json),
        "stability": _read_json(args.stability_summary_json),
        "fps": _read_json(args.fps_summary_json),
        "regression": _read_json(args.regression_summary_json),
    }
    report = build_final_delivery_report(inputs)
    write_final_delivery_report(report, args.output_dir)
    print(f"最终交付报告已生成：{args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
