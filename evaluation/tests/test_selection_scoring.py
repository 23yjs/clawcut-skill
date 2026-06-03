from __future__ import annotations

import unittest

from evaluation.selection_scoring import (
    compute_generic_selection_score,
    compute_guided_selection_score,
)


SEMANTIC = [
    {
        "segment_id": "seg_001",
        "start": 0,
        "end": 10,
        "description": "高价值片段",
        "default_highlight_score": 5,
        "avoid_by_default": False,
    },
    {
        "segment_id": "seg_002",
        "start": 10,
        "end": 20,
        "description": "中价值片段",
        "default_highlight_score": 4,
        "avoid_by_default": False,
    },
    {
        "segment_id": "seg_003",
        "start": 20,
        "end": 30,
        "description": "片尾",
        "default_highlight_score": 1,
        "avoid_by_default": True,
    },
]


class SelectionScoringTests(unittest.TestCase):
    def test_generic_optimal_value(self) -> None:
        result = compute_generic_selection_score(
            [{"start": 0, "end": 10}],
            SEMANTIC,
            duration_budget=10,
            duration_score=1.0,
        )
        self.assertEqual(result["generic_value_optimal"], 10.0)
        self.assertEqual(result["generic_value_actual"], 10.0)
        self.assertEqual(result["generic_value_score"], 1.0)
        self.assertEqual(result["generic_value_mode"], "budgeted")
        self.assertEqual(result["default_highlight_precision"], 1.0)
        self.assertEqual(result["generic_core_score"], 1.0)
        self.assertEqual(result["selection_score_v1"], 100.0)

    def test_generic_over_budget_does_not_increase_positive_value(self) -> None:
        result = compute_generic_selection_score(
            [{"start": 0, "end": 20}],
            SEMANTIC,
            duration_budget=10,
            duration_score=1.0,
        )
        self.assertEqual(result["generic_value_actual"], 10.0)
        self.assertEqual(result["generic_value_score"], 1.0)
        self.assertEqual(result["generic_value_mode"], "budgeted")
        self.assertEqual(result["generic_core_score"], 1.0)

    def test_generic_without_duration_budget_uses_true_highlight_denominator(self) -> None:
        semantic = [
            {
                "segment_id": "seg_001",
                "start": 0,
                "end": 10,
                "description": "高光 1",
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
            {
                "segment_id": "seg_002",
                "start": 10,
                "end": 20,
                "description": "高光 2",
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
        ]
        result = compute_generic_selection_score(
            [{"start": 0, "end": 10}],
            semantic,
            duration_budget=None,
            duration_score=1.0,
        )
        self.assertEqual(result["generic_value_mode"], "full_gt_highlight_only")
        self.assertEqual(result["generic_value_optimal"], 20.0)
        self.assertEqual(result["generic_value_actual"], 10.0)
        self.assertEqual(result["generic_value_score"], 0.5)

    def test_generic_without_duration_budget_excludes_score_three_auxiliary_from_denominator(self) -> None:
        semantic = [
            {
                "segment_id": "seg_highlight",
                "start": 0,
                "end": 10,
                "description": "核心高光",
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
            {
                "segment_id": "seg_auxiliary",
                "start": 10,
                "end": 30,
                "description": "合理辅助过程",
                "default_highlight_score": 3,
                "avoid_by_default": False,
            },
            {
                "segment_id": "seg_regular",
                "start": 30,
                "end": 60,
                "description": "普通内容",
                "default_highlight_score": 1,
                "avoid_by_default": False,
            },
        ]
        result = compute_generic_selection_score(
            [{"start": 0, "end": 10}],
            semantic,
            duration_budget=None,
            duration_score=1.0,
        )
        self.assertEqual(result["generic_value_mode"], "full_gt_highlight_only")
        self.assertEqual(result["generic_value_optimal"], 10.0)
        self.assertEqual(result["generic_value_actual"], 10.0)
        self.assertEqual(result["generic_value_score"], 1.0)
        self.assertEqual(result["default_highlight_precision"], 1.0)
        self.assertEqual(result["generic_core_score"], 1.0)
        self.assertEqual(result["selection_score_v1"], 100.0)

    def test_generic_default_highlight_precision_all_highlight(self) -> None:
        result = compute_generic_selection_score(
            [{"start": 0, "end": 10}],
            SEMANTIC,
            duration_budget=10,
            duration_score=1.0,
        )
        self.assertEqual(result["default_highlight_duration"], 10.0)
        self.assertEqual(result["default_highlight_precision"], 1.0)
        self.assertEqual(result["generic_core_score"], 1.0)
        self.assertEqual(result["selection_score_v1"], 100.0)

    def test_generic_default_highlight_precision_with_avoid_content(self) -> None:
        result = compute_generic_selection_score(
            [{"start": 0, "end": 10}, {"start": 20, "end": 30}],
            SEMANTIC,
            duration_budget=20,
            duration_score=1.0,
        )
        self.assertEqual(result["pred_total_duration"], 20.0)
        self.assertEqual(result["default_highlight_duration"], 10.0)
        self.assertEqual(result["default_highlight_precision"], 0.5)
        self.assertEqual(result["generic_value_score"], 0.588)
        self.assertEqual(result["generic_core_score"], 0.541)
        self.assertEqual(result["default_avoid_compliance_score"], 0.5)
        self.assertEqual(result["selection_score_v1"], 27.027)

    def test_generic_low_value_non_avoid_content_lowers_selection_score(self) -> None:
        semantic = [
            {
                "segment_id": "seg_highlight",
                "start": 0,
                "end": 10,
                "description": "核心高光",
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
            {
                "segment_id": "seg_low_value",
                "start": 10,
                "end": 30,
                "description": "普通低价值过程",
                "default_highlight_score": 1,
                "avoid_by_default": False,
            },
        ]
        result = compute_generic_selection_score(
            [{"start": 0, "end": 30}],
            semantic,
            duration_budget=30,
            duration_score=1.0,
        )
        self.assertEqual(result["generic_value_score"], 1.0)
        self.assertEqual(result["default_highlight_duration"], 10.0)
        self.assertEqual(result["default_highlight_precision"], 0.333)
        self.assertEqual(result["generic_core_score"], 0.5)
        self.assertEqual(result["default_avoid_compliance_score"], 1.0)
        self.assertEqual(result["selection_score_v1"], 50.0)

    def test_generic_high_precision_but_lower_value_choice_is_not_full_score(self) -> None:
        semantic = [
            {
                "segment_id": "seg_best",
                "start": 0,
                "end": 10,
                "description": "最高价值片段",
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
            {
                "segment_id": "seg_secondary",
                "start": 10,
                "end": 20,
                "description": "次高价值片段",
                "default_highlight_score": 4,
                "avoid_by_default": False,
            },
        ]
        result = compute_generic_selection_score(
            [{"start": 10, "end": 20}],
            semantic,
            duration_budget=10,
            duration_score=1.0,
        )
        self.assertEqual(result["generic_value_optimal"], 10.0)
        self.assertEqual(result["generic_value_actual"], 7.0)
        self.assertEqual(result["generic_value_score"], 0.7)
        self.assertEqual(result["default_highlight_precision"], 1.0)
        self.assertEqual(result["generic_core_score"], 0.824)
        self.assertAlmostEqual(result["selection_score_v1"], 82.4, places=1)

    def test_generic_avoid_content_gets_precision_and_avoid_penalties(self) -> None:
        semantic = [
            {
                "segment_id": "seg_highlight",
                "start": 0,
                "end": 10,
                "description": "核心高光",
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
            {
                "segment_id": "seg_avoid",
                "start": 10,
                "end": 20,
                "description": "片尾导流",
                "default_highlight_score": 1,
                "avoid_by_default": True,
            },
        ]
        result = compute_generic_selection_score(
            [{"start": 0, "end": 20}],
            semantic,
            duration_budget=20,
            duration_score=1.0,
        )
        self.assertEqual(result["generic_value_score"], 1.0)
        self.assertEqual(result["default_highlight_precision"], 0.5)
        self.assertEqual(result["generic_core_score"], 0.667)
        self.assertEqual(result["default_avoid_compliance_score"], 0.5)
        self.assertEqual(result["selection_score_v1"], 33.333)

    def test_generic_default_highlight_precision_excludes_score_three_context(self) -> None:
        semantic = [
            {
                "segment_id": "seg_highlight",
                "start": 0,
                "end": 10,
                "description": "真正高光",
                "default_highlight_score": 4,
                "avoid_by_default": False,
            },
            {
                "segment_id": "seg_context",
                "start": 10,
                "end": 20,
                "description": "有效但非高光上下文",
                "default_highlight_score": 3,
                "avoid_by_default": False,
            },
        ]
        result = compute_generic_selection_score(
            [{"start": 0, "end": 20}],
            semantic,
            duration_budget=20,
            duration_score=1.0,
        )
        self.assertEqual(result["default_highlight_duration"], 10.0)
        self.assertEqual(result["default_highlight_precision"], 0.5)
        self.assertEqual(result["generic_core_score"], 0.667)

    def test_generic_default_highlight_precision_empty_output(self) -> None:
        result = compute_generic_selection_score(
            [],
            SEMANTIC,
            duration_budget=10,
            duration_score=1.0,
        )
        self.assertEqual(result["generic_value_score"], 0.0)
        self.assertEqual(result["default_highlight_duration"], 0.0)
        self.assertEqual(result["default_highlight_precision"], 0.0)
        self.assertEqual(result["generic_core_score"], 0.0)
        self.assertEqual(result["selection_score_v1"], 0.0)

    def test_guided_precision_coverage_f1(self) -> None:
        result = compute_guided_selection_score(
            [{"start": 0, "end": 5}, {"start": 20, "end": 25}],
            SEMANTIC,
            relevant_segment_ids=["seg_001"],
            forbidden_segment_ids=[],
            selection_scope="exclusive",
            duration_budget=10,
            duration_score=1.0,
        )
        self.assertEqual(result["relevant_duration_precision"], 0.5)
        self.assertEqual(result["relevant_duration_coverage"], 0.5)
        self.assertEqual(result["relevant_duration_f1"], 0.5)
        self.assertEqual(result["coverage_mode"], "budgeted")
        self.assertEqual(result["guided_core_score"], 0.5)
        self.assertEqual(result["selection_score_v1"], 50.0)

    def test_guided_without_duration_budget_uses_full_relevant_gt_coverage(self) -> None:
        semantic = [
            {
                "segment_id": "seg_001",
                "start": 0,
                "end": 10,
                "description": "目标 1",
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
            {
                "segment_id": "seg_002",
                "start": 10,
                "end": 20,
                "description": "目标 2",
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
        ]
        result = compute_guided_selection_score(
            [{"start": 0, "end": 10}],
            semantic,
            relevant_segment_ids=["seg_001", "seg_002"],
            forbidden_segment_ids=[],
            selection_scope="preferential",
            duration_budget=None,
            duration_score=1.0,
        )
        self.assertEqual(result["coverage_mode"], "full_gt")
        self.assertEqual(result["relevant_gt_total_duration"], 20.0)
        self.assertEqual(result["matched_relevant_duration"], 10.0)
        self.assertEqual(result["relevant_duration_coverage"], 0.5)

    def test_guided_with_duration_budget_keeps_budgeted_coverage(self) -> None:
        semantic = [
            {
                "segment_id": "seg_001",
                "start": 0,
                "end": 10,
                "description": "目标 1",
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
            {
                "segment_id": "seg_002",
                "start": 10,
                "end": 20,
                "description": "目标 2",
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
        ]
        result = compute_guided_selection_score(
            [{"start": 0, "end": 10}],
            semantic,
            relevant_segment_ids=["seg_001", "seg_002"],
            forbidden_segment_ids=[],
            selection_scope="preferential",
            duration_budget=10,
            duration_score=1.0,
        )
        self.assertEqual(result["coverage_mode"], "budgeted")
        self.assertEqual(result["relevant_gt_total_duration"], 20.0)
        self.assertEqual(result["matched_relevant_duration"], 10.0)
        self.assertEqual(result["relevant_duration_coverage"], 1.0)

    def test_forbidden_duration_penalty(self) -> None:
        result = compute_guided_selection_score(
            [{"start": 0, "end": 10}, {"start": 20, "end": 30}],
            SEMANTIC,
            relevant_segment_ids=["seg_001"],
            forbidden_segment_ids=["seg_003"],
            selection_scope="preferential",
            duration_budget=10,
            duration_score=1.0,
        )
        self.assertEqual(result["forbidden_overlap_duration"], 10.0)
        self.assertEqual(result["forbidden_duration_ratio"], 0.5)
        self.assertEqual(result["forbidden_compliance_score"], 0.5)
        self.assertEqual(result["guided_core_score"], 0.85)
        self.assertEqual(result["selection_score_v1"], 42.5)

    def test_duration_score_scales_total(self) -> None:
        result = compute_generic_selection_score(
            [{"start": 0, "end": 10}],
            SEMANTIC,
            duration_budget=10,
            duration_score=0.5,
        )
        self.assertEqual(result["selection_score_v1"], 50.0)


if __name__ == "__main__":
    unittest.main()
