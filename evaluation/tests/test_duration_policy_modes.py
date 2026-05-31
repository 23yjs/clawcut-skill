from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "clawcut-video-highlight" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from plan_validator import validate_plan  # noqa: E402
from run_skill import _build_duration_policy, _write_success_summary  # noqa: E402


def _base_plan(duration_policy: dict, start: float = 0.0, end: float = 5.0) -> dict:
    return {
        "duration_policy": duration_policy,
        "video_type": "ecommerce_product",
        "type_confidence": 0.8,
        "user_intent": "剪出高光",
        "highlight_definition": {
            "goal": "剪出高光",
            "must_include": [],
            "avoid": [],
            "selection_logic": "选择高光",
            "definition_source": "user_instruction + video_content + model_inference",
            "scoring_rubric": {
                "query_relevance": "0-5",
                "highlight_value": "0-5",
                "completeness": "0-5",
                "visual_audio_evidence": "0-5",
            },
        },
        "chunking_strategy": {"method": "llm_guided_semantic_chunking", "reason": "测试"},
        "chunks": [
            {
                "id": "chunk_01",
                "start": 0.0,
                "end": 10.0,
                "title": "测试 chunk",
                "summary": "测试",
                "semantic_role": "测试",
                "expected_highlight_value": "high",
            }
        ],
        "chunk_reviews": [
            {
                "chunk_id": "chunk_01",
                "summary": "测试",
                "scores": {
                    "query_relevance": 5,
                    "highlight_value": 5,
                    "completeness": 5,
                    "visual_audio_evidence": 5,
                },
                "overall_score": 20,
                "should_select": True,
                "refined_start": start,
                "refined_end": end,
                "reason": "测试",
            }
        ],
        "final_segments": [
            {
                "start": start,
                "end": end,
                "title": "测试片段",
                "role": "高光",
                "source_chunk_id": "chunk_01",
                "reason": "测试",
            }
        ],
        "excluded_highlights": [],
        "self_check": {"pass": True, "issues": []},
        "overall_rationale": "测试",
    }


class DurationPolicyModeTests(unittest.TestCase):
    def test_default_mode_is_bounded_auto(self) -> None:
        policy = _build_duration_policy(233.289, None)
        self.assertEqual(policy["duration_policy_mode"], "bounded_auto")
        self.assertAlmostEqual(policy["recommended_duration"], 34.993)

    def test_bounded_auto_without_target_uses_existing_formula(self) -> None:
        policy = _build_duration_policy(120.0, None, "bounded_auto")
        self.assertEqual(policy["duration_policy_mode"], "bounded_auto")
        self.assertEqual(policy["recommended_duration"], 18.0)
        self.assertEqual(policy["selected_target_duration"], 18.0)
        self.assertEqual(policy["allowed_min_duration"], 15.0)
        self.assertEqual(policy["allowed_max_duration"], 60.0)

    def test_llm_free_without_target_does_not_apply_15_percent(self) -> None:
        policy = _build_duration_policy(120.0, None, "llm_free")
        self.assertEqual(policy["duration_policy_mode"], "llm_free")
        self.assertIsNone(policy["recommended_duration"])
        self.assertIsNone(policy["selected_target_duration"])

    def test_llm_free_without_target_does_not_apply_15_second_floor(self) -> None:
        policy = _build_duration_policy(10.0, None, "llm_free")
        self.assertEqual(policy["allowed_min_duration"], 0.001)
        self.assertEqual(policy["allowed_max_duration"], 10.0)
        self.assertIsNone(policy["recommended_duration"])

    def test_llm_free_without_target_does_not_apply_60_second_ceiling(self) -> None:
        policy = _build_duration_policy(1000.0, None, "llm_free")
        self.assertEqual(policy["allowed_max_duration"], 1000.0)
        self.assertIsNone(policy["selected_target_duration"])

    def test_explicit_target_overrides_both_modes(self) -> None:
        for mode in ("bounded_auto", "llm_free"):
            policy = _build_duration_policy(233.0, 30.0, mode)
            self.assertEqual(policy["duration_policy_mode"], mode)
            self.assertTrue(policy["user_specified_duration"])
            self.assertEqual(policy["selected_target_duration"], 30.0)
            self.assertEqual(policy["allowed_min_duration"], 30.0)
            self.assertEqual(policy["allowed_max_duration"], 30.0)

    def test_llm_free_output_cannot_exceed_video_duration(self) -> None:
        policy = _build_duration_policy(10.0, None, "llm_free")
        policy["selected_target_duration"] = 11.0
        plan = _base_plan(policy, 0.0, 11.0)
        result = validate_plan(plan, 10.0, 11.0, {"validation": {"min_segment_duration": 1.0}})
        self.assertFalse(result["ok"])
        self.assertTrue(any("不得超过原视频时长" in error or "超出视频时长范围" in error for error in result["errors"]))

    def test_llm_free_output_must_be_positive(self) -> None:
        policy = _build_duration_policy(10.0, None, "llm_free")
        policy["selected_target_duration"] = 0.0
        plan = _base_plan(policy, 0.0, 0.0)
        result = validate_plan(plan, 10.0, 0.0, {"validation": {"min_segment_duration": 1.0}})
        self.assertFalse(result["ok"])
        self.assertTrue(any("必须大于 0" in error or "start < end" in error for error in result["errors"]))

    def test_result_summary_contains_duration_policy_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = _build_duration_policy(120.0, None, "llm_free")
            policy["selected_target_duration"] = 12.0
            policy["final_total_duration"] = 12.0
            plan = _base_plan(policy, 0.0, 12.0)
            paths = {
                "highlight": root / "videos" / "highlight.mp4",
                "segments": root / "reports" / "segments.json",
                "report": root / "reports" / "report.md",
                "log": root / "logs" / "run.log",
            }
            summary = root / "reports" / "result_summary.json"
            _write_success_summary(
                summary,
                paths,
                Path("data/input/demo.MP4"),
                "剪出高光",
                plan,
                {
                    "selected_target_duration": 12.0,
                    "total_duration": 12.0,
                    "duration_delta": 0.0,
                    "warnings": [],
                },
                None,
            )
            payload = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual(payload["duration_policy"]["duration_policy_mode"], "llm_free")


if __name__ == "__main__":
    unittest.main()
