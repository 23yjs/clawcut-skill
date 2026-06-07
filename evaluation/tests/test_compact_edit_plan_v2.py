from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "clawcut-video-highlight" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from plan_validator import validate_plan  # noqa: E402


def _plan(segment_count: int = 1):
    return {
        "video_type": "cooking_tutorial",
        "video_type_reason": "包含连续烹饪步骤和成品展示。",
        "highlight_definition": {
            "must_keep": ["关键制作步骤", "成品展示"],
            "avoid": ["重复操作"],
        },
        "final_segments": [
            {"start": float(index * 2), "end": float(index * 2 + 1.5), "title": f"片段{index}"}
            for index in range(segment_count)
        ],
    }


def test_compact_edit_plan_v2_valid_without_chunks_reviews_or_excluded():
    plan = _plan()
    result = validate_plan(
        plan,
        video_duration=60.0,
        target_duration=None,
        config={
            "duration_policy": {"duration_policy_mode": "llm_free"},
            "planning": {"max_final_segments": 24, "max_title_chars": 30},
        },
    )
    assert result["ok"] is True
    assert plan["edit_plan_schema_version"] == "compact_edit_plan_v2"
    assert "chunks" not in plan
    assert "chunk_reviews" not in plan


def test_compact_edit_plan_v2_rejects_more_than_24_segments():
    result = validate_plan(
        _plan(25),
        video_duration=100.0,
        target_duration=None,
        config={
            "duration_policy": {"duration_policy_mode": "llm_free"},
            "planning": {"max_final_segments": 24, "max_title_chars": 30},
        },
    )
    assert result["ok"] is False
    assert any("final_segments 数量不能超过 24" in error for error in result["errors"])
