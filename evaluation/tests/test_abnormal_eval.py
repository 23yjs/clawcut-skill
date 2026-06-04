from __future__ import annotations

from evaluation.run_abnormal_eval import build_abnormal_summary, validate_abnormal_cases


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
