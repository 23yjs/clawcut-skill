from __future__ import annotations

import json
from pathlib import Path

from evaluation.ark_resolver_client import ArkResolverConfig
from evaluation.auto_eval import AutoEvalConfig, run_auto_eval
from evaluation.dover_quality import DoverConfig


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _prepare(tmp_path: Path) -> AutoEvalConfig:
    gt_dir = tmp_path / "gt"
    _write_json(
        gt_dir / "demo.json",
        {
            "video_id": "demo",
            "video_path": "data/input/demo.MP4",
            "video_type": "sports",
            "duration_seconds": 60,
            "video_summary": "测试视频。",
            "semantic_segments": [
                {
                    "segment_id": "seg_001",
                    "start": 0,
                    "end": 10,
                    "description": "一次明确高光。",
                    "default_highlight_score": 5,
                    "avoid_by_default": False,
                },
                {
                    "segment_id": "seg_002",
                    "start": 10,
                    "end": 60,
                    "description": "普通上下文。",
                    "default_highlight_score": 2,
                    "avoid_by_default": False,
                },
            ],
        },
    )
    skill_output_dir = tmp_path / "skill"
    _write_json(
        skill_output_dir / "reports" / "result_summary.json",
        {
            "status": "success",
            "input_video": "data/input/demo.MP4",
            "instruction": "剪出高光",
            "target_duration": None,
            "skill_backend_requested": "ark",
            "skill_backend_used": "ark",
            "fallback_used": False,
            "source_video_duration": 60,
            "duration_policy": {
                "duration_policy_mode": "bounded_auto",
                "user_specified_duration": False,
                "user_target_duration": None,
                "recommended_duration": 15,
                "selected_target_duration": 15,
                "final_total_duration": 8,
            },
            "selected_target_duration": 15,
            "final_total_duration": 8,
            "highlight_video": str(skill_output_dir / "videos" / "highlight.mp4"),
            "segments_json": str(skill_output_dir / "reports" / "segments.json"),
            "run_log": str(skill_output_dir / "logs" / "run.log"),
        },
    )
    _write_json(skill_output_dir / "reports" / "segments.json", {"final_segments": [{"start": 1, "end": 9}]})
    (skill_output_dir / "videos").mkdir(parents=True, exist_ok=True)
    (skill_output_dir / "videos" / "highlight.mp4").write_bytes(b"fake")
    (skill_output_dir / "logs").mkdir(parents=True, exist_ok=True)
    (skill_output_dir / "logs" / "run.log").write_text("使用的 LLM backend：ark\n", encoding="utf-8")
    return AutoEvalConfig(
        input_video=Path("data/input/demo.MP4"),
        instruction="剪出高光",
        target_duration=None,
        skill_output_dir=skill_output_dir,
        gt_dir=gt_dir,
        output_dir=tmp_path / "eval",
        resolver_config=ArkResolverConfig(model="test-model"),
    )


def _patch_resolver(monkeypatch) -> None:
    def fake_resolve_instruction_with_ark(**kwargs):
        return (
            {
                "instruction_mode": "generic",
                "selection_scope": "not_applicable",
                "resolution_status": "resolved",
                "use_default_highlights": True,
                "relevant_segment_ids": [],
                "forbidden_segment_ids": [],
                "unresolved_requirements": [],
                "resolver_reason": "默认高光。",
            },
            {"resolver_model": "test-model"},
        )

    monkeypatch.setattr("evaluation.auto_eval.resolve_instruction_with_ark", fake_resolve_instruction_with_ark)


def test_technical_quality_failure_skips_dover_and_judge(tmp_path, monkeypatch):
    config = _prepare(tmp_path)
    config.judge_video_url = "https://example.com/highlight.mp4"
    _patch_resolver(monkeypatch)

    monkeypatch.setattr(
        "evaluation.auto_eval.check_technical_quality",
        lambda **kwargs: {"technical_quality_passed": False, "technical_quality_errors": ["decode failed"]},
    )

    def fail_dover(*args, **kwargs):
        raise AssertionError("technical 失败时不应执行 DOVER")

    def fail_judge(*args, **kwargs):
        raise AssertionError("technical 失败时不应执行 Judge")

    monkeypatch.setattr("evaluation.auto_eval.evaluate_dover_quality", fail_dover)
    monkeypatch.setattr("evaluation.auto_eval.run_aesthetic_judge", fail_judge)
    result = run_auto_eval(config)
    assert result["evaluation_status"] == "technical_quality_failed"
    assert result["perceptual_video_quality"]["dover_status"] == "skipped"
    assert result["editing_experience"]["status"] == "skipped"
    assert result["final_score_v2"] is None


def test_dover_unavailable_does_not_block_selection_pending(tmp_path, monkeypatch):
    config = _prepare(tmp_path)
    config.dover_config = DoverConfig(enabled=True)
    _patch_resolver(monkeypatch)
    monkeypatch.setattr(
        "evaluation.auto_eval.check_technical_quality",
        lambda **kwargs: {"technical_quality_passed": True, "rendered_duration": 8.0},
    )
    monkeypatch.setattr(
        "evaluation.auto_eval.evaluate_dover_quality",
        lambda *args, **kwargs: {"provider": "DOVER", "status": "unavailable", "dover_status": "unavailable"},
    )
    result = run_auto_eval(config)
    assert result["evaluation_status"] == "selection_scored_aesthetic_pending"
    assert result["perceptual_video_quality"]["dover_status"] == "unavailable"
    assert result["aesthetic_score_v1"] is None


def test_judge_result_keeps_old_and_new_score_fields(tmp_path, monkeypatch):
    config = _prepare(tmp_path)
    config.judge_video_url = "https://example.com/highlight.mp4"
    _patch_resolver(monkeypatch)
    monkeypatch.setattr(
        "evaluation.auto_eval.check_technical_quality",
        lambda **kwargs: {"technical_quality_passed": True, "rendered_duration": 8.0},
    )
    monkeypatch.setattr(
        "evaluation.auto_eval.evaluate_dover_quality",
        lambda *args, **kwargs: {"provider": "DOVER", "status": "disabled", "dover_status": "disabled"},
    )
    monkeypatch.setattr(
        "evaluation.auto_eval.run_aesthetic_judge",
        lambda **kwargs: {
            "judge_status": "scored",
            "editing_experience_score_v1": 80.0,
            "aesthetic_score_v1": 80.0,
            "aesthetic_score_v1_deprecated_alias": True,
            "judge_stability_warning": False,
            "judge_manual_review_required": False,
            "judge_confidence": 0.9,
        },
    )
    result = run_auto_eval(config)
    assert result["evaluation_status"] == "scored_complete"
    assert result["editing_experience_score_v1"] == 80.0
    assert result["aesthetic_score_v1"] == 80.0
    assert result["final_score_v2"] is not None
