from __future__ import annotations

import pytest

from evaluation.aesthetic_judge import summarize_judge_runs, validate_aesthetic_judge_result
from evaluation.ark_aesthetic_judge_client import parse_aesthetic_judge_json


def _judge_payload(**overrides):
    payload = {
        "clip_boundary_completeness": {"score": 4, "reason": "边界完整"},
        "transition_coherence": {"score": 3, "reason": "转场略跳"},
        "pacing_and_conciseness": {"score": 4, "reason": "节奏清楚"},
        "audio_visual_continuity": {"score": 4, "reason": "音画连续"},
        "standalone_watchability": {"score": 5, "reason": "可独立观看"},
        "editing_experience_score_v1": 80,
        "judge_confidence": 0.86,
        "judge_summary": "基本可观看。",
        "manual_review_recommended": False,
    }
    payload.update(overrides)
    return payload


def test_valid_judge_json_computes_aesthetic_score():
    result = validate_aesthetic_judge_result(_judge_payload())
    assert result["aesthetic_score_v1"] == 80.0
    assert result["editing_experience_score_v1"] == 80.0
    assert result["aesthetic_score_v1_deprecated_alias"] is True
    assert result["judge_confidence"] == 0.86
    assert result["issues"] == []


def test_valid_judge_issues_pass():
    result = validate_aesthetic_judge_result(
        _judge_payload(
            issues=[
                {
                    "issue_type": "action_truncation",
                    "severity": "high",
                    "description": "投篮动作尚未完成，片段已经结束。",
                }
            ]
        )
    )
    assert result["issues"] == [
        {
            "issue_type": "action_truncation",
            "severity": "high",
            "description": "投篮动作尚未完成，片段已经结束。",
        }
    ]


def test_score_out_of_range_fails():
    payload = _judge_payload()
    payload["transition_coherence"]["score"] = 6
    with pytest.raises(ValueError):
        validate_aesthetic_judge_result(payload)


def test_invalid_issue_type_fails():
    payload = _judge_payload(
        issues=[
            {
                "issue_type": "bad_type",
                "severity": "high",
                "description": "非法类型。",
            }
        ]
    )
    with pytest.raises(ValueError):
        validate_aesthetic_judge_result(payload)


def test_invalid_issue_severity_fails():
    payload = _judge_payload(
        issues=[
            {
                "issue_type": "abrupt_transition",
                "severity": "critical",
                "description": "非法等级。",
            }
        ]
    )
    with pytest.raises(ValueError):
        validate_aesthetic_judge_result(payload)


def test_low_confidence_forces_manual_review_summary():
    first = validate_aesthetic_judge_result(_judge_payload(judge_confidence=0.49))
    summary = summarize_judge_runs([first], [{}])
    assert summary["judge_manual_review_required"] is True


def test_repeats_use_median_and_detect_instability():
    low_payload = _judge_payload()
    high_payload = _judge_payload()
    for key in ["clip_boundary_completeness", "transition_coherence", "pacing_and_conciseness", "audio_visual_continuity", "standalone_watchability"]:
        low_payload[key]["score"] = 1
        high_payload[key]["score"] = 5
    low = validate_aesthetic_judge_result(low_payload)
    high = validate_aesthetic_judge_result(high_payload)
    summary = summarize_judge_runs([low, high], [{}, {}])
    assert summary["judge_score_median"] == 60.0
    assert summary["judge_score_range"] == 80.0
    assert summary["judge_stability_warning"] is True


def test_summary_counts_issue_types():
    first = validate_aesthetic_judge_result(
        _judge_payload(
            issues=[
                {
                    "issue_type": "action_truncation",
                    "severity": "high",
                    "description": "动作截断。",
                },
                {
                    "issue_type": "abrupt_transition",
                    "severity": "medium",
                    "description": "转场突兀。",
                },
            ]
        )
    )
    second = validate_aesthetic_judge_result(
        _judge_payload(
            issues=[
                {
                    "issue_type": "abrupt_transition",
                    "severity": "low",
                    "description": "另一次转场突兀。",
                }
            ]
        )
    )
    summary = summarize_judge_runs([first, second], [{}, {}])
    assert summary["judge_issue_counts"] == {
        "abrupt_transition": 2,
        "action_truncation": 1,
    }


def test_parse_json_code_block():
    parsed = parse_aesthetic_judge_json('```json\n{"judge_status":"scored"}\n```')
    assert parsed["judge_status"] == "scored"


def test_invalid_json_fails():
    with pytest.raises(Exception):
        parse_aesthetic_judge_json("not json")
