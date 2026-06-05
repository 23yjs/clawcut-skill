from __future__ import annotations

import csv
import json

from evaluation.human_readable_report import build_summary, classify_case, write_reports


def test_classify_case_distinguishes_official_fallback_and_failed() -> None:
    assert classify_case({"evaluation_status": "scored_complete", "final_score_v2": "90", "technical_quality_passed": "True"}) == "excellent"
    assert classify_case({"evaluation_status": "scored_complete", "final_score_v2": "75", "technical_quality_passed": "True"}) == "usable"
    assert classify_case({"evaluation_status": "selection_scored_aesthetic_pending", "selection_score_v1": "99", "technical_quality_passed": "True"}) == "pending"
    assert classify_case({"evaluation_status": "technical_quality_failed", "technical_quality_passed": "False"}) == "failed"
    assert classify_case({"collection_status": "diagnostic_skill_fallback", "fallback_used": "True"}) == "diagnostic"


def test_write_reports_creates_human_and_technical_outputs(tmp_path) -> None:
    rows = [
        {
            "case_id": "case_ok",
            "video_id": "demo",
            "test_type": "baseline_generic",
            "priority": "baseline",
            "instruction": "帮我剪辑一下这个视频",
            "evaluation_status": "scored_complete",
            "technical_quality_passed": "True",
            "selection_score_v1": "88",
            "final_score_v2": "90",
            "fallback_used": "False",
            "skill_llm_total_tokens": "123",
            "elapsed_seconds": "8.5",
        },
        {
            "case_id": "case_bad",
            "video_id": "demo_bad",
            "test_type": "high_dynamic",
            "priority": "priority",
            "instruction": "剪出关键击杀",
            "evaluation_status": "scored_complete",
            "technical_quality_passed": "True",
            "selection_score_v1": "42",
            "fallback_used": "False",
        },
        {
            "case_id": "case_diag",
            "video_id": "demo_diag",
            "test_type": "baseline_generic",
            "priority": "baseline",
            "instruction": "帮我剪辑一下这个视频",
            "collection_status": "diagnostic_skill_fallback",
            "fallback_used": "True",
        },
        {
            "case_id": "case_pending",
            "video_id": "demo_pending",
            "test_type": "baseline_generic",
            "priority": "baseline",
            "instruction": "帮我剪辑一下这个视频",
            "evaluation_status": "selection_scored_aesthetic_pending",
            "selection_score_v1": "99",
            "technical_quality_passed": "True",
        }
    ]
    summary = write_reports(rows=rows, output_dir=tmp_path)
    assert summary["case_count"] == 4
    assert (tmp_path / "report.html").exists()
    assert (tmp_path / "summary.md").exists()
    assert (tmp_path / "technical_appendix.html").exists()
    assert (tmp_path / "cases" / "case_ok.html").exists()
    report_html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "未检测到明显黑屏" in report_html
    assert "按测试类型汇总" in report_html
    assert "典型成功案例" in report_html
    assert "case_ok" in report_html
    assert "case_bad" in report_html
    assert summary["by_test_type"][0]["name"] == "baseline_generic"
    assert summary["case_studies"]["representative_successes"][0]["case_id"] == "case_ok"
    assert summary["case_studies"]["representative_failures"][0]["case_id"] == "case_bad"
    assert summary["case_studies"]["diagnostic_samples"][0]["case_id"] == "case_diag"
    assert summary["case_studies"]["pending_samples"][0]["case_id"] == "case_pending"
    assert summary["average_score"] == 90


def test_build_summary_counts_cases() -> None:
    summary = build_summary(
        [
            {"evaluation_status": "scored_complete", "final_score_v2": 91, "technical_quality_passed": True},
            {"collection_status": "diagnostic_openclaw_fallback"},
            {"evaluation_status": "selection_scored_aesthetic_pending", "selection_score_v1": 99, "technical_quality_passed": True},
        ]
    )
    assert summary["case_count"] == 3
    assert summary["conclusion_counts"]["优秀"] == 1
    assert summary["conclusion_counts"]["仅诊断"] == 1
    assert summary["conclusion_counts"]["待完成成片体验评审"] == 1
    assert summary["average_score"] == 91
    assert "case_studies" in summary
