from __future__ import annotations

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
