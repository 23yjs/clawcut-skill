from __future__ import annotations

import json
from pathlib import Path


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_official_cases_have_required_design_fields() -> None:
    rows = _read_jsonl(Path("data/eval/cases.official.v1.jsonl"))
    required = {
        "case_id",
        "video_id",
        "video_filename",
        "input_video",
        "skill_output_dir",
        "instruction",
        "test_type",
        "tested_capability",
        "why_this_video",
        "expected_good_behavior",
        "known_risk",
        "priority",
    }
    assert len(rows) >= 56
    assert all(required <= set(row) for row in rows)
    assert all(row["input_video"].startswith("/home/node/.openclaw/workspace/data/input/") for row in rows)
    assert all(row["input_video"].endswith(row["video_filename"]) for row in rows)
    assert all("/home/node/.openclaw/workspace/outputs/openclaw_collection_v1/" in row["skill_output_dir"] for row in rows)
    assert all(row["case_id"] in row["skill_output_dir"] for row in rows)
    assert len({row["case_id"] for row in rows}) == len(rows)
    assert sum(1 for row in rows if row["test_type"] == "baseline_generic") == 31
    assert {row["priority"] for row in rows} >= {"baseline", "priority", "extended"}
    assert {
        "baseline_generic",
        "specific_following",
        "conflict_exclusion",
        "duration_constrained",
        "high_dynamic",
        "long_dense_video",
    } <= {row["test_type"] for row in rows}


def test_abnormal_cases_are_separate_from_official_cases() -> None:
    official_ids = {row["case_id"] for row in _read_jsonl(Path("data/eval/cases.official.v1.jsonl"))}
    abnormal = _read_jsonl(Path("data/eval/abnormal_cases.v1.jsonl"))
    assert all(row["case_id"] not in official_ids for row in abnormal)
    assert all(row["should_enter_official_scoring"] is False for row in abnormal)


def test_official_v2_cases_are_ready_for_openclaw_dispatch() -> None:
    rows = _read_jsonl(Path("data/eval/cases.official.v2.jsonl"))
    required = {
        "case_id",
        "video_id",
        "video_filename",
        "input_video",
        "skill_output_dir",
        "instruction",
        "target_duration",
        "llm_video_url",
        "test_type",
        "tested_capability",
        "why_this_video",
        "expected_good_behavior",
        "known_risk",
        "priority",
    }
    assert len(rows) == 72
    assert all(required <= set(row) for row in rows)
    assert len({row["case_id"] for row in rows}) == len(rows)
    assert len({row["skill_output_dir"] for row in rows}) == len(rows)
    assert all(row["input_video"].startswith("/home/node/.openclaw/workspace/data/input/") for row in rows)
    assert all(row["input_video"].endswith(row["video_filename"]) for row in rows)
    assert all("/home/node/.openclaw/workspace/outputs/openclaw_collection_v2/" in row["skill_output_dir"] for row in rows)
    assert all(row["case_id"] in row["skill_output_dir"] for row in rows)
    assert all(str(row["llm_video_url"]).startswith("https://") for row in rows)
    assert sum(1 for row in rows if row["test_type"] == "baseline_generic") == 35
    assert {row["priority"] for row in rows} == {"baseline", "priority", "extended"}
    assert {row["test_type"] for row in rows} == {
        "baseline_generic",
        "specific_following",
        "conflict_exclusion",
        "duration_constrained",
    }
    assert any(row["video_id"] == "knowledge-share-demo5" for row in rows)
    assert Path("data/eval/knowledge-share-demo5.json").exists()
    assert any(row["video_id"] == "pet_training_demo" for row in rows)
    assert Path("data/eval/pet_training_demo.json").exists()
