from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.ark_resolver_client import ArkResolverConfig, ArkResolverError
from evaluation.auto_eval import AutoEvalConfig, run_auto_eval


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _gt_payload() -> dict:
    return {
        "video_id": "demo",
        "video_path": "data/input/demo.MP4",
        "video_type": "ecommerce_product",
        "duration_seconds": 120,
        "video_summary": "一个商品展示测试视频。",
        "semantic_segments": [
            {
                "segment_id": "seg_001",
                "start": 0,
                "end": 9,
                "description": "商品外观展示。",
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
            {
                "segment_id": "seg_002",
                "start": 9,
                "end": 100,
                "description": "商品核心卖点讲解和中间上下文。",
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
            {
                "segment_id": "seg_003",
                "start": 100,
                "end": 120,
                "description": "片尾账号信息。",
                "default_highlight_score": 1,
                "avoid_by_default": True,
            },
        ],
    }


def _prepare_files(tmp_path: Path) -> AutoEvalConfig:
    gt_dir = tmp_path / "gt"
    _write_json(gt_dir / "demo.json", _gt_payload())
    skill_output_dir = tmp_path / "skill_output"
    _write_json(
        skill_output_dir / "reports" / "result_summary.json",
        {
            "status": "success",
            "input_video": "data/input/demo.MP4",
            "instruction": "测试指令",
            "target_duration": None,
            "skill_backend_requested": "ark",
            "skill_backend_used": "ark",
            "fallback_used": False,
            "source_video_duration": 120,
            "skill_prompt_version": "highlight_prompt_v1",
            "skill_model": "test-skill-model",
            "duration_policy": {
                "duration_policy_mode": "bounded_auto",
                "user_specified_duration": False,
                "user_target_duration": None,
                "recommended_duration": 18.0,
                "selected_target_duration": 18.0,
                "allowed_min_duration": 15.0,
                "allowed_max_duration": 60.0,
                "final_total_duration": 15.0,
                "duration_policy_reason": "测试",
            },
            "selected_target_duration": 18.0,
            "final_total_duration": 15.0,
            "highlight_video": str(skill_output_dir / "videos" / "highlight.mp4"),
            "segments_json": str(skill_output_dir / "reports" / "segments.json"),
            "run_log": str(skill_output_dir / "logs" / "run.log"),
        },
    )
    _write_json(
        skill_output_dir / "reports" / "segments.json",
        {
            "final_segments": [
                {"start": 1, "end": 8, "title": "商品外观"},
                {"start": 101, "end": 109, "title": "片尾账号"},
            ]
        },
    )
    (skill_output_dir / "videos").mkdir(parents=True, exist_ok=True)
    (skill_output_dir / "videos" / "highlight.mp4").write_bytes(b"fake mp4")
    (skill_output_dir / "logs").mkdir(parents=True, exist_ok=True)
    (skill_output_dir / "logs" / "run.log").write_text("使用的 LLM backend：ark\n", encoding="utf-8")
    return AutoEvalConfig(
        input_video=Path("data/input/demo.MP4"),
        instruction="测试指令",
        target_duration=None,
        skill_output_dir=skill_output_dir,
        gt_dir=gt_dir,
        output_dir=tmp_path / "auto_eval",
        resolver_config=ArkResolverConfig(model="test-model"),
    )


def _resolver_result(**overrides) -> dict:
    result = {
        "instruction_mode": "specific",
        "selection_scope": "preferential",
        "resolution_status": "resolved",
        "use_default_highlights": False,
        "relevant_segment_ids": ["seg_001"],
        "forbidden_segment_ids": [],
        "unresolved_requirements": [],
        "resolver_reason": "命中商品外观。",
    }
    result.update(overrides)
    return result


def _metadata() -> dict:
    return {
        "resolver_model": "test-model",
        "resolver_prompt_version": "resolver_v1",
        "resolver_latency_seconds": 0.01,
        "resolver_attempt_count": 1,
        "resolver_http_status": 200,
        "resolver_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _patch_resolver(monkeypatch, result: dict) -> None:
    def fake_resolve_instruction_with_ark(**kwargs):
        return result, _metadata()

    monkeypatch.setattr("evaluation.auto_eval.resolve_instruction_with_ark", fake_resolve_instruction_with_ark)
    _patch_technical(monkeypatch)


def _patch_technical(monkeypatch) -> None:
    def fake_check_technical_quality(**kwargs):
        return {
            "technical_quality_passed": True,
            "technical_quality_errors": [],
            "technical_quality_warnings": [],
            "planned_total_duration": 15.0,
            "rendered_duration": 15.0,
            "rendered_duration_delta": 0.0,
            "rendered_duration_error_ratio": 0.0,
            "video_stream_present": True,
            "source_has_audio": True,
            "highlight_has_audio": True,
            "audio_stream_consistent": True,
            "decode_success": True,
            "decode_error": "",
            "black_frame_duration": 0.0,
            "black_frame_ratio": 0.0,
            "compression_ratio": 0.125,
            "selected_source_union_duration": 15.0,
            "duplicate_source_duration": 0.0,
            "duplicate_source_ratio": 0.0,
        }

    monkeypatch.setattr("evaluation.auto_eval.check_technical_quality", fake_check_technical_quality)


def _assert_common_outputs(output_dir: Path) -> None:
    assert (output_dir / "resolver_request.json").exists()
    assert (output_dir / "resolver_response.json").exists()
    assert (output_dir / "resolver_metadata.json").exists()
    assert (output_dir / "generated_case.json").exists()
    assert (output_dir / "evaluation_result.json").exists()
    assert (output_dir / "eval_report.md").exists()


def test_auto_eval_generic_scores_and_writes_files(tmp_path, monkeypatch):
    config = _prepare_files(tmp_path)
    _patch_resolver(
        monkeypatch,
        _resolver_result(
            instruction_mode="generic",
            selection_scope="not_applicable",
            use_default_highlights=True,
            relevant_segment_ids=[],
            forbidden_segment_ids=[],
        ),
    )
    result = run_auto_eval(config)
    _assert_common_outputs(config.output_dir)
    assert result["evaluation_status"] == "selection_scored_aesthetic_pending"
    assert result["evaluation_scope"] == "official"
    assert result["selection_score_v1"] is not None
    assert "default_highlight_f1" in result["legacy_metrics"]


def test_auto_eval_specific_scores_reference_metrics(tmp_path, monkeypatch):
    config = _prepare_files(tmp_path)
    _patch_resolver(monkeypatch, _resolver_result(relevant_segment_ids=["seg_001"]))
    result = run_auto_eval(config)
    _assert_common_outputs(config.output_dir)
    assert result["evaluation_status"] == "selection_scored_aesthetic_pending"
    assert result["evaluation_scope"] == "official"
    assert result["time_metrics"]["relevant_duration_coverage"] == 0.778


def test_auto_eval_conflict_scores_forbidden_metrics(tmp_path, monkeypatch):
    config = _prepare_files(tmp_path)
    _patch_resolver(
        monkeypatch,
        _resolver_result(
            instruction_mode="conflict",
            relevant_segment_ids=["seg_001"],
            forbidden_segment_ids=["seg_003"],
            resolver_reason="保留外观，排除片尾。",
        ),
    )
    result = run_auto_eval(config)
    _assert_common_outputs(config.output_dir)
    assert result["semantic_metrics"]["forbidden_segment_hit_count"] == 1
    assert result["legacy_metrics"]["forbidden_segment_violation_rate"] == 0.5
    assert result["time_metrics"]["forbidden_duration_ratio"] == 0.533


def test_auto_eval_unresolved_requires_manual_review(tmp_path, monkeypatch):
    config = _prepare_files(tmp_path)
    _patch_resolver(
        monkeypatch,
        _resolver_result(
            instruction_mode="unresolved",
            selection_scope="unknown",
            resolution_status="unresolved",
            relevant_segment_ids=[],
            unresolved_requirements=["GT 没有语气兴奋度信息"],
            resolver_reason="GT 信息不足。",
        ),
    )
    result = run_auto_eval(config)
    _assert_common_outputs(config.output_dir)
    assert result["evaluation_status"] == "manual_review_required"
    assert result["legacy_metrics"] is None
    assert result["final_score"] is None


def test_auto_eval_resolver_failure_writes_failure_result(tmp_path, monkeypatch):
    config = _prepare_files(tmp_path)
    _patch_technical(monkeypatch)

    def fake_resolve_instruction_with_ark(**kwargs):
        raise ArkResolverError("resolver failed")

    monkeypatch.setattr("evaluation.auto_eval.resolve_instruction_with_ark", fake_resolve_instruction_with_ark)
    result = run_auto_eval(config)
    assert (config.output_dir / "resolver_request.json").exists()
    assert (config.output_dir / "evaluation_result.json").exists()
    assert (config.output_dir / "eval_report.md").exists()
    assert (config.output_dir / "resolver_response.json").exists()
    assert result["evaluation_status"] == "resolver_failed"
    assert result["final_score"] is None
    assert "resolver failed" in result["error_message"]


def test_find_nested_skill_output(tmp_path, monkeypatch):
    gt_dir = tmp_path / "gt"
    _write_json(gt_dir / "demo.json", _gt_payload())
    skill_root = tmp_path / "outputs"
    _write_json(
        skill_root / "demo" / "reports" / "result_summary.json",
        {
            "status": "success",
            "input_video": "data/input/demo.MP4",
            "instruction": "测试指令",
            "target_duration": 20,
            "skill_backend_requested": "ark",
            "skill_backend_used": "ark",
            "fallback_used": False,
            "source_video_duration": 120,
            "skill_prompt_version": "highlight_prompt_v1",
            "duration_policy": {
                "duration_policy_mode": "bounded_auto",
                "user_specified_duration": True,
                "user_target_duration": 20,
                "recommended_duration": None,
                "selected_target_duration": 20,
                "allowed_min_duration": 20,
                "allowed_max_duration": 20,
                "final_total_duration": 7,
                "duration_policy_reason": "测试",
            },
            "selected_target_duration": 20,
            "final_total_duration": 7,
            "highlight_video": str(skill_root / "demo" / "videos" / "highlight.mp4"),
            "segments_json": str(skill_root / "demo" / "reports" / "segments.json"),
            "run_log": str(skill_root / "demo" / "logs" / "run.log"),
        },
    )
    _write_json(
        skill_root / "demo" / "reports" / "segments.json",
        {"final_segments": [{"start": 1, "end": 8}]},
    )
    (skill_root / "demo" / "videos").mkdir(parents=True, exist_ok=True)
    (skill_root / "demo" / "videos" / "highlight.mp4").write_bytes(b"fake mp4")
    (skill_root / "demo" / "logs").mkdir(parents=True, exist_ok=True)
    (skill_root / "demo" / "logs" / "run.log").write_text("使用的 LLM backend：ark\n", encoding="utf-8")
    config = AutoEvalConfig(
        input_video=Path("data/input/demo.MP4"),
        instruction="测试指令",
        target_duration=20,
        skill_output_dir=skill_root,
        gt_dir=gt_dir,
        output_dir=tmp_path / "auto_nested",
        resolver_config=ArkResolverConfig(model="test-model"),
    )
    _patch_resolver(monkeypatch, _resolver_result(relevant_segment_ids=["seg_001"]))
    result = run_auto_eval(config)
    assert result["evaluation_status"] == "selection_scored_aesthetic_pending"
    assert result["segments_json"].endswith("outputs/demo/reports/segments.json")


def test_auto_eval_llm_free_is_diagnostic_only(tmp_path, monkeypatch):
    config = _prepare_files(tmp_path)
    summary_path = config.skill_output_dir / "reports" / "result_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["duration_policy"]["duration_policy_mode"] = "llm_free"
    summary["duration_policy"]["user_specified_duration"] = False
    summary["duration_policy"]["user_target_duration"] = None
    summary["duration_policy"]["recommended_duration"] = None
    summary["duration_policy"]["selected_target_duration"] = 15.0
    summary["duration_policy"]["final_total_duration"] = 15.0
    summary["selected_target_duration"] = 15.0
    summary["final_total_duration"] = 15.0
    _write_json(summary_path, summary)
    _patch_resolver(
        monkeypatch,
        _resolver_result(
            instruction_mode="generic",
            selection_scope="not_applicable",
            use_default_highlights=True,
            relevant_segment_ids=[],
            forbidden_segment_ids=[],
        ),
    )
    result = run_auto_eval(config)
    assert result["evaluation_status"] == "diagnostic_only"
    assert result["evaluation_scope"] == "diagnostic_only"
    assert result["selection_score_v1"] is None


def test_auto_eval_uses_frozen_generated_case_without_resolver(tmp_path, monkeypatch):
    config = _prepare_files(tmp_path)
    frozen_case = {
        "case_id": "frozen_demo",
        "video_id": "demo",
        "instruction": "测试指令",
        "target_duration": None,
        **_resolver_result(relevant_segment_ids=["seg_001"]),
        "resolver_backend": "ark",
        "resolver_prompt_version": "resolver_v2",
    }
    frozen_path = tmp_path / "frozen.json"
    _write_json(frozen_path, frozen_case)
    config.generated_case_json = frozen_path

    def fail_resolver(**kwargs):
        raise AssertionError("不应调用 Ark Resolver")

    monkeypatch.setattr("evaluation.auto_eval.resolve_instruction_with_ark", fail_resolver)
    _patch_technical(monkeypatch)
    result = run_auto_eval(config)
    assert result["evaluation_status"] == "selection_scored_aesthetic_pending"
    assert result["resolver_metadata"]["resolver_backend"] == "frozen"
    assert (config.output_dir / "generated_case.json").exists()


def test_auto_eval_with_judge_video_url_outputs_final_score_v2(tmp_path, monkeypatch):
    config = _prepare_files(tmp_path)
    config.judge_video_url = "https://example.com/highlight.mp4?debug=1"
    _patch_resolver(
        monkeypatch,
        _resolver_result(
            instruction_mode="generic",
            selection_scope="not_applicable",
            use_default_highlights=True,
            relevant_segment_ids=[],
            forbidden_segment_ids=[],
        ),
    )

    def fake_judge(**kwargs):
        return {
            "judge_status": "scored",
            "aesthetic_score_v1": 80.0,
            "judge_stability_warning": False,
            "judge_manual_review_required": False,
            "judge_confidence": 0.9,
        }

    monkeypatch.setattr("evaluation.auto_eval.run_aesthetic_judge", fake_judge)
    result = run_auto_eval(config)
    assert result["evaluation_status"] == "scored_complete"
    assert result["aesthetic_score_v1"] == 80.0
    assert result["final_score_v2"] is not None
    request = json.loads((config.output_dir / "aesthetic_judge_request.json").read_text(encoding="utf-8"))
    assert "debug=1" not in str(request)


def test_auto_eval_mock_fallback_is_diagnostic_only(tmp_path, monkeypatch):
    config = _prepare_files(tmp_path)
    summary_path = config.skill_output_dir / "reports" / "result_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["skill_backend_used"] = "mock"
    summary["fallback_used"] = True
    _write_json(summary_path, summary)

    def fail_resolver(**kwargs):
        raise AssertionError("mock fallback 不应进入 Resolver")

    monkeypatch.setattr("evaluation.auto_eval.resolve_instruction_with_ark", fail_resolver)
    _patch_technical(monkeypatch)
    result = run_auto_eval(config)
    assert result["evaluation_status"] == "diagnostic_only"
    assert result["selection_score_v1"] is None
    assert result["final_score_v2"] is None


def test_frozen_generated_case_mismatch_errors(tmp_path):
    config = _prepare_files(tmp_path)
    frozen_case = {
        "case_id": "frozen_demo",
        "video_id": "other",
        "instruction": "测试指令",
        "target_duration": None,
        **_resolver_result(relevant_segment_ids=["seg_001"]),
    }
    frozen_path = tmp_path / "bad_frozen.json"
    _write_json(frozen_path, frozen_case)
    config.generated_case_json = frozen_path
    with pytest.raises(ValueError):
        run_auto_eval(config)


def test_frozen_generated_case_unknown_segment_errors(tmp_path):
    config = _prepare_files(tmp_path)
    frozen_case = {
        "case_id": "frozen_demo",
        "video_id": "demo",
        "instruction": "测试指令",
        "target_duration": None,
        **_resolver_result(relevant_segment_ids=["seg_x"]),
    }
    frozen_path = tmp_path / "bad_segment.json"
    _write_json(frozen_path, frozen_case)
    config.generated_case_json = frozen_path
    with pytest.raises(Exception):
        run_auto_eval(config)
