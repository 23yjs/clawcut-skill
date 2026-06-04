from __future__ import annotations

import json

from evaluation.final_delivery_report import build_final_delivery_report, main, render_markdown


def _complete_inputs() -> dict:
    return {
        "official_summary": {
            "human_report": {
                "case_count": 2,
                "average_score": 82.5,
                "fallback_count": 0,
                "failed_count": 0,
                "conclusion_counts": {"优秀": 1, "基本可用": 1},
                "by_test_type": [
                    {
                        "name": "baseline_generic",
                        "case_count": 2,
                        "average_score": 82.5,
                        "conclusion_counts": {"优秀": 1, "基本可用": 1},
                    }
                ],
                "case_studies": {
                    "representative_successes": [
                        {"case_id": "case_ok", "why": "核心内容选择表现较好。", "suggestion": "纳入回归。"}
                    ],
                    "representative_failures": [],
                    "diagnostic_samples": [],
                },
            }
        },
        "readiness": {
            "case_count": 2,
            "ready_for_official_eval": 2,
            "not_ready_count": 0,
            "status_counts": {"ready": 2},
        },
        "abnormal": {
            "status": "ready",
            "case_count": 3,
            "abnormal_type_counts": {"missing_video_path": 1, "no_audio_video": 1},
            "errors": [],
        },
        "stability": {
            "case_count": 1,
            "attempt_count": 3,
            "overall": {
                "official_success_rate": 1.0,
                "skill_fallback_rate": 0.0,
                "openclaw_fallback_rate": 0.0,
                "estimated_cost": 0.12,
            },
            "cases": [
                {"case_id": "case_ok", "max_latency_seconds": 12.5, "estimated_cost": 0.12},
            ],
        },
        "fps": {
            "case_count": 1,
            "result_count": 3,
            "rows": [{"case_id": "fps_case", "result_available": True, "short_highlight_missed": False}],
            "recommendations": [{"case_id": "fps_case", "recommendation": "fps=1 已命中。"}],
        },
        "regression": {
            "gate_passed": True,
            "average_score_delta": 1.0,
            "failed_case_regressions": 0,
            "fallback_regressions": 0,
            "technical_quality_regressions": 0,
            "gate_failures": [],
        },
    }


def test_final_delivery_report_summarizes_complete_evidence() -> None:
    report = build_final_delivery_report(_complete_inputs())
    assert report["overall_conclusion"] == "基本可用"
    assert report["missing_sections"] == []
    assert report["official_effect"]["case_count"] == 2
    assert report["stability_cost"]["slowest_cases"][0]["case_id"] == "case_ok"
    markdown = render_markdown(report)
    assert "ClawCut 最终评测交付报告" in markdown
    assert "典型成功案例" in markdown
    assert "版本回归" in markdown


def test_final_delivery_report_marks_missing_sections() -> None:
    inputs = _complete_inputs()
    inputs["regression"] = None
    report = build_final_delivery_report(inputs)
    assert report["overall_conclusion"] == "证据未闭环"
    assert report["missing_sections"] == ["regression"]


def test_final_delivery_report_cli_writes_outputs(tmp_path) -> None:
    paths = {}
    for name, payload in _complete_inputs().items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        paths[name] = path
    output = tmp_path / "final"
    assert (
        main(
            [
                "--official-summary-json",
                str(paths["official_summary"]),
                "--readiness-json",
                str(paths["readiness"]),
                "--abnormal-summary-json",
                str(paths["abnormal"]),
                "--stability-summary-json",
                str(paths["stability"]),
                "--fps-summary-json",
                str(paths["fps"]),
                "--regression-summary-json",
                str(paths["regression"]),
                "--output-dir",
                str(output),
            ]
        )
        == 0
    )
    assert (output / "final_delivery_report.json").exists()
    assert (output / "FINAL_EVALUATION_REPORT.md").exists()
