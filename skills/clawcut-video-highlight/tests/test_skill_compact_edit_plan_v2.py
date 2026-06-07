from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "skills" / "clawcut-video-highlight" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from plan_validator import validate_plan  # noqa: E402


def test_skill_compact_edit_plan_v2_accepts_minimal_schema():
    plan = {
        "video_type": "game",
        "video_type_reason": "包含持续操作和结果反馈。",
        "highlight_definition": {
            "must_keep": ["关键击杀"],
            "avoid": ["普通跑图"],
        },
        "final_segments": [{"start": 1.0, "end": 4.0, "title": "关键击杀"}],
    }
    result = validate_plan(
        plan,
        video_duration=20.0,
        target_duration=None,
        config={
            "duration_policy": {"duration_policy_mode": "llm_free"},
            "planning": {"max_final_segments": 24, "max_title_chars": 30},
        },
    )
    assert result["ok"] is True
