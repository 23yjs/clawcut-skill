from __future__ import annotations

import json

from evaluation.run_abnormal_eval import build_abnormal_summary, main, validate_abnormal_cases


def test_abnormal_cases_must_not_enter_official_scoring() -> None:
    errors = validate_abnormal_cases(
        [
            {
                "case_id": "bad",
                "abnormal_type": "missing_video_path",
                "description": "missing",
                "expected_error_type": "missing_input_video",
                "expected_behavior": "fail clearly",
                "should_enter_official_scoring": True,
            }
        ]
    )
    assert any("must not enter official scoring" in error for error in errors)


def test_no_audio_is_legal_input_condition() -> None:
    errors = validate_abnormal_cases(
        [
            {
                "case_id": "no_audio",
                "abnormal_type": "no_audio_video",
                "description": "no audio",
                "expected_error_type": "decode_failed",
                "expected_behavior": "should not fail",
                "should_enter_official_scoring": False,
            }
        ]
    )
    assert any("expected_error_type should be none" in error for error in errors)


def test_abnormal_summary_counts_types() -> None:
    summary = build_abnormal_summary(
        [
            {
                "case_id": "missing",
                "abnormal_type": "missing_video_path",
                "description": "missing",
                "expected_error_type": "missing_input_video",
                "expected_behavior": "fail clearly",
                "should_enter_official_scoring": False,
            }
        ]
    )
    assert summary["status"] == "ready"
    assert summary["abnormal_type_counts"]["missing_video_path"] == 1


def test_abnormal_result_summary_passes_expected_failure_and_legal_input() -> None:
    cases = [
        {
            "case_id": "missing",
            "abnormal_type": "missing_video_path",
            "description": "missing",
            "expected_error_type": "missing_input_video",
            "expected_behavior": "fail clearly",
            "should_enter_official_scoring": False,
        },
        {
            "case_id": "no_audio",
            "abnormal_type": "no_audio_video",
            "description": "no audio",
            "expected_error_type": "none",
            "expected_behavior": "legal input",
            "should_enter_official_scoring": False,
        },
    ]
    summary = build_abnormal_summary(
        cases,
        [
            {
                "case_id": "missing",
                "status": "failed",
                "error_type": "missing_input_video",
                "result_summary_exists": True,
                "run_log_exists": True,
                "highlight_video_exists": False,
            },
            {
                "case_id": "no_audio",
                "status": "success",
                "error_type": "none",
                "result_summary_exists": True,
                "run_log_exists": True,
                "highlight_video_exists": True,
            },
        ],
    )
    assert summary["status"] == "ready"
    assert summary["passed_result_count"] == 2
    assert summary["failed_result_count"] == 0
    assert summary["result_rows"][0]["abnormal_type_label"] == "视频路径不存在"
    assert summary["result_rows"][0]["actual_error_type_label"] == "输入视频不存在"
    assert summary["result_rows"][1]["actual_error_type_label"] == "无错误"


def test_abnormal_result_summary_detects_misleading_highlight_and_missing_logs() -> None:
    cases = [
        {
            "case_id": "bad",
            "abnormal_type": "ark_invalid_json",
            "description": "invalid json",
            "expected_error_type": "invalid_llm_json",
            "expected_behavior": "block invalid model output",
            "should_enter_official_scoring": False,
        }
    ]
    summary = build_abnormal_summary(
        cases,
        [
            {
                "case_id": "bad",
                "status": "failed",
                "error_type": "invalid_llm_json",
                "result_summary_exists": False,
                "run_log_exists": False,
                "highlight_video_exists": True,
            }
        ],
    )
    row = summary["result_rows"][0]
    assert summary["status"] == "failed"
    assert row["passed"] is False
    assert "misleading highlight" in row["reasons"]
    assert "missing result_summary" in row["reasons"]
    assert "missing run_log" in row["reasons"]


def test_abnormal_result_summary_marks_not_run() -> None:
    summary = build_abnormal_summary(
        [
            {
                "case_id": "missing",
                "abnormal_type": "missing_video_path",
                "description": "missing",
                "expected_error_type": "missing_input_video",
                "expected_behavior": "fail clearly",
                "should_enter_official_scoring": False,
            }
        ],
        [],
    )
    assert summary["status"] == "failed"
    assert summary["not_run_count"] == 1
    assert summary["result_rows"][0]["actual_status"] == "not_run"


def test_abnormal_cli_accepts_results_jsonl(tmp_path) -> None:
    cases = tmp_path / "cases.jsonl"
    results = tmp_path / "results.jsonl"
    output = tmp_path / "abnormal"
    case = {
        "case_id": "missing",
        "abnormal_type": "missing_video_path",
        "description": "missing",
        "expected_error_type": "missing_input_video",
        "expected_behavior": "fail clearly",
        "should_enter_official_scoring": False,
    }
    result = {
        "case_id": "missing",
        "status": "failed",
        "error_type": "missing_input_video",
        "result_summary_exists": True,
        "run_log_exists": True,
        "highlight_video_exists": False,
    }
    cases.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    results.write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
    assert main(["--cases", str(cases), "--results-jsonl", str(results), "--output-dir", str(output)]) == 0
    payload = json.loads((output / "abnormal_summary.json").read_text(encoding="utf-8"))
    assert payload["passed_result_count"] == 1
    assert "Actual Result Checks" in (output / "abnormal_summary.md").read_text(encoding="utf-8")
