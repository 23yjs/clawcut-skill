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
