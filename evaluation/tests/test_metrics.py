from __future__ import annotations

import unittest

from evaluation.metrics import (
    compute_case_score,
    compute_default_highlight_metrics,
    compute_must_avoid_violation,
    compute_must_cover_tag_coverage,
    match_pred_to_semantic_segments,
    temporal_iou,
)


class TemporalMetricTests(unittest.TestCase):
    def test_temporal_iou_exact_overlap(self) -> None:
        self.assertEqual(temporal_iou({"start": 0, "end": 10}, {"start": 0, "end": 10}), 1.0)

    def test_temporal_iou_partial_overlap(self) -> None:
        self.assertAlmostEqual(
            temporal_iou({"start": 0, "end": 10}, {"start": 5, "end": 15}),
            5 / 15,
        )

    def test_temporal_iou_no_overlap(self) -> None:
        self.assertEqual(temporal_iou({"start": 0, "end": 4}, {"start": 5, "end": 8}), 0.0)


class MatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.semantic_segments = [
            {
                "segment_id": "seg_1",
                "start": 0,
                "end": 10,
                "description": "外观展示",
                "tags": ["appearance"],
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
            {
                "segment_id": "seg_2",
                "start": 8,
                "end": 18,
                "description": "功能演示",
                "tags": ["function"],
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
        ]

    def test_match_one_semantic_segment(self) -> None:
        result = match_pred_to_semantic_segments([{"start": 1, "end": 5}], self.semantic_segments)
        self.assertEqual(result["matched_segment_ids"], ["seg_1"])
        self.assertIn("appearance", result["matched_tags"])

    def test_match_multiple_semantic_segments(self) -> None:
        result = match_pred_to_semantic_segments([{"start": 7, "end": 12}], self.semantic_segments)
        self.assertEqual(result["matched_segment_ids"], ["seg_1", "seg_2"])

    def test_match_no_semantic_segment(self) -> None:
        result = match_pred_to_semantic_segments([{"start": 30, "end": 35}], self.semantic_segments)
        self.assertEqual(result["matched_segment_ids"], [])


class TagMetricTests(unittest.TestCase):
    def test_must_cover_all_hit(self) -> None:
        result = compute_must_cover_tag_coverage(["appearance", "function"], ["appearance", "function"])
        self.assertEqual(result["must_cover_coverage"], 1.0)
        self.assertEqual(result["missed_must_cover_tags"], [])

    def test_must_cover_partial_hit(self) -> None:
        result = compute_must_cover_tag_coverage(["appearance"], ["appearance", "function"])
        self.assertEqual(result["must_cover_coverage"], 0.5)
        self.assertEqual(result["missed_must_cover_tags"], ["function"])

    def test_must_cover_empty_targets(self) -> None:
        result = compute_must_cover_tag_coverage([], [])
        self.assertEqual(result["must_cover_coverage"], 1.0)

    def test_must_avoid_no_violation(self) -> None:
        result = compute_must_avoid_violation(["appearance"], ["outro"], [])
        self.assertEqual(result["violation_rate"], 0.0)

    def test_must_avoid_has_violation(self) -> None:
        result = compute_must_avoid_violation(
            ["appearance", "outro"],
            ["outro"],
            [
                {
                    "pred_index": 0,
                    "start": 0,
                    "end": 5,
                    "matches": [{"segment_id": "seg_outro", "tags": ["outro"]}],
                }
            ],
        )
        self.assertEqual(result["violation_rate"], 1.0)
        self.assertEqual(result["violated_tags"], ["outro"])
        self.assertEqual(len(result["violating_segments"]), 1)


class DefaultHighlightTests(unittest.TestCase):
    def test_default_highlight_ignores_avoid_by_default(self) -> None:
        semantic_segments = [
            {
                "segment_id": "good",
                "start": 0,
                "end": 10,
                "tags": ["good"],
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
            {
                "segment_id": "outro",
                "start": 20,
                "end": 30,
                "tags": ["outro"],
                "default_highlight_score": 5,
                "avoid_by_default": True,
            },
        ]
        result = compute_default_highlight_metrics([{"start": 0, "end": 8}], semantic_segments)
        self.assertEqual(result["default_highlight_target_count"], 1)
        self.assertEqual(result["default_highlight_recall"], 1.0)
        self.assertEqual(result["missed_default_highlights"], [])


class CaseScoreTests(unittest.TestCase):
    def test_uncovered_case_is_manual_only(self) -> None:
        result = compute_case_score(
            {"annotation_coverage": "uncovered", "judge_mode": "manual_only"},
            {},
        )
        self.assertIsNone(result["final_score"])
        self.assertEqual(result["evaluation_status"], "manual_only")


if __name__ == "__main__":
    unittest.main()
