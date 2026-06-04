from __future__ import annotations

import csv
import json

from evaluation.human_readable_report import build_summary, classify_case, write_reports


def test_classify_case_distinguishes_official_fallback_and_failed() -> None:
    assert classify_case({"evaluation_status": "scored_complete", "final_score_v2": "90", "technical_quality_passed": "True"}) == "excellent"
    assert classify_case({"evaluation_status": "scored_complete", "selection_score_v1": "75", "technical_quality_passed": "True"}) == "usable"
    assert classify_case({"evaluation_status": "technical_quality_failed", "technical_quality_passed": "False"}) == "failed"
    assert classify_case({"collection_status": "diagnostic_skill_fallback", "fallback_used": "True"}) == "diagnostic"


def test_write_reports_creates_human_and_technical_outputs(tmp_path) -> None:
    rows = [
        {
            "case_id": "case_ok",
            "video_id": "demo",
            "instruction": "帮我剪辑一下这个视频",
            "evaluation_status": "scored_complete",
            "technical_quality_passed": "True",
            "selection_score_v1": "88",
            "final_score_v2": "90",
            "fallback_used": "False",
            "skill_llm_total_tokens": "123",
            "elapsed_seconds": "8.5",
        }
    ]
    summary = write_reports(rows=rows, output_dir=tmp_path)
    assert summary["case_count"] == 1
    assert (tmp_path / "report.html").exists()
    assert (tmp_path / "summary.md").exists()
    assert (tmp_path / "technical_appendix.html").exists()
    assert (tmp_path / "cases" / "case_ok.html").exists()
    assert "未检测到明显黑屏" in (tmp_path / "report.html").read_text(encoding="utf-8")


def test_build_summary_counts_cases() -> None:
    summary = build_summary(
        [
            {"evaluation_status": "scored_complete", "final_score_v2": 91, "technical_quality_passed": True},
            {"collection_status": "diagnostic_openclaw_fallback"},
        ]
    )
    assert summary["case_count"] == 2
    assert summary["conclusion_counts"]["优秀"] == 1
    assert summary["conclusion_counts"]["仅诊断"] == 1
