from __future__ import annotations

import json

from evaluation.run_fps_sensitivity_eval import summarize_fps_sensitivity


def test_fps_sensitivity_marks_critical_action_hit() -> None:
    summary = summarize_fps_sensitivity(
        [
            {
                "case_id": "fps_case",
                "video_id": "game",
                "critical_action_window": {"start": 10, "end": 11},
                "fps_values": [1, 2],
            }
        ],
        [
            {
                "case_id": "fps_case",
                "video_fps": 1,
                "final_segments": [{"start": 0, "end": 5}],
                "skill_llm_total_tokens": 100,
            },
            {
                "case_id": "fps_case",
                "video_fps": 2,
                "final_segments": [{"start": 10.2, "end": 10.8}],
                "skill_llm_total_tokens": 180,
            },
        ],
    )
    rows = summary["rows"]
    assert rows[0]["critical_action_hit"] is False
    assert rows[0]["short_highlight_missed"] is True
    assert rows[1]["critical_action_hit"] is True
    assert "fps=2" in summary["recommendations"][0]["recommendation"]


def test_fps_sensitivity_recommends_no_global_change_when_fps_one_hits() -> None:
    summary = summarize_fps_sensitivity(
        [
            {
                "case_id": "fps_case",
                "video_id": "sports",
                "critical_action_window": "3-4",
                "fps_values": [1],
            }
        ],
        [
            {
                "case_id": "fps_case",
                "video_fps": 1,
                "final_segments": [{"start": 2.5, "end": 3.5}],
            }
        ],
    )
    assert "暂不建议全局提高 fps" in summary["recommendations"][0]["recommendation"]


def test_fps_sensitivity_allows_missing_critical_action_window() -> None:
    summary = summarize_fps_sensitivity(
        [
            {
                "case_id": "fps_case",
                "video_id": "game",
                "fps_values": [1],
            }
        ],
        [
            {
                "case_id": "fps_case",
                "video_fps": 1,
                "final_segments": [{"start": 2.5, "end": 3.5}],
            }
        ],
    )
    row = summary["rows"][0]
    assert row["critical_action_window_available"] is False
    assert row["critical_action_hit"] is None
    assert row["short_highlight_missed"] is None
    assert summary["missing_critical_action_window_count"] == 1
    assert "缺少 critical_action_window" in summary["recommendations"][0]["recommendation"]


def test_fps_sensitivity_accepts_multiple_critical_action_windows() -> None:
    summary = summarize_fps_sensitivity(
        [
            {
                "case_id": "fps_case",
                "video_id": "game",
                "critical_action_window": [
                    {"start": 10, "end": 11},
                    {"start": 20, "end": 21},
                ],
                "fps_values": [1],
            }
        ],
        [
            {
                "case_id": "fps_case",
                "video_fps": 1,
                "final_segments": [{"start": 20.2, "end": 20.8}],
            }
        ],
    )
    row = summary["rows"][0]
    assert row["critical_action_window_count"] == 2
    assert row["critical_action_hit"] is True
    assert "source_cases" in summary
    assert summary["source_cases"][0]["critical_action_windows"] == [
        {"start": 10.0, "end": 11.0},
        {"start": 20.0, "end": 21.0},
    ]


def test_fps_sensitivity_reads_segments_from_result_summary(tmp_path) -> None:
    result_summary = tmp_path / "result_summary.json"
    result_summary.write_text(
        json.dumps({"final_segments": [{"start": 20.2, "end": 20.8}]}),
        encoding="utf-8",
    )
    summary = summarize_fps_sensitivity(
        [
            {
                "case_id": "fps_case",
                "video_id": "game",
                "critical_action_windows": [
                    {"start": 10, "end": 11},
                    {"start": 20, "end": 21},
                ],
                "fps_values": [1],
            }
        ],
        [
            {
                "source_case_id": "fps_case",
                "video_fps": 1,
                "result_summary": str(result_summary),
            }
        ],
    )
    assert summary["rows"][0]["critical_action_hit"] is True
    assert summary["rows"][0]["final_segments"][0]["duration"] == 0.6
    assert summary["rows"][0]["result_summary"] == str(result_summary)
