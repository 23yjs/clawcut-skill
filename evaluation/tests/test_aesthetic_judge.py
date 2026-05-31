from __future__ import annotations

import pytest

from evaluation.aesthetic_judge import summarize_judge_runs, validate_aesthetic_judge_result
from evaluation.ark_aesthetic_judge_client import parse_aesthetic_judge_json


def _judge_payload(**overrides):
    payload = {
        "judge_version": "aesthetic_judge_v1",
        "judge_status": "scored",
        "scores": {
            "clip_boundary_completeness": 4,
            "transition_coherence": 3,
            "pacing_and_conciseness": 4,
            "audio_visual_continuity": 4,
            "standalone_watchability": 5,
        },
        "strengths": ["重点清楚"],
        "issues": [],
        "manual_review_required": False,
        "judge_confidence": 0.86,
        "overall_reason": "基本可观看。",
    }
    payload.update(overrides)
    return payload


def test_valid_judge_json_computes_aesthetic_score():
    result = validate_aesthetic_judge_result(_judge_payload())
    assert result["aesthetic_score_v1"] == 80.0
    assert result["judge_confidence"] == 0.86


def test_score_out_of_range_fails():
    payload = _judge_payload()
    payload["scores"]["transition_coherence"] = 6
    with pytest.raises(ValueError):
        validate_aesthetic_judge_result(payload)


def test_low_confidence_forces_manual_review_summary():
    first = validate_aesthetic_judge_result(_judge_payload(judge_confidence=0.49))
    summary = summarize_judge_runs([first], [{}])
    assert summary["judge_manual_review_required"] is True


def test_repeats_use_median_and_detect_instability():
    low = validate_aesthetic_judge_result(_judge_payload(scores={key: 1 for key in _judge_payload()["scores"]}))
    high = validate_aesthetic_judge_result(_judge_payload(scores={key: 5 for key in _judge_payload()["scores"]}))
    summary = summarize_judge_runs([low, high], [{}, {}])
    assert summary["judge_score_median"] == 60.0
    assert summary["judge_score_range"] == 80.0
    assert summary["judge_stability_warning"] is True


def test_parse_json_code_block():
    parsed = parse_aesthetic_judge_json('```json\n{"judge_status":"scored"}\n```')
    assert parsed["judge_status"] == "scored"


def test_invalid_json_fails():
    with pytest.raises(Exception):
        parse_aesthetic_judge_json("not json")
