from __future__ import annotations

import unittest

from evaluation.metrics import (
    _expand_segment_for_tolerance,
    _temporal_match_details,
    compute_default_highlight_metrics,
)


class TemporalBoundaryToleranceTests(unittest.TestCase):
    def test_raw_match_success(self) -> None:
        details = _temporal_match_details(
            {"start": 1, "end": 8},
            {"start": 0, "end": 9},
            iou_threshold=0.1,
            overlap_ratio_threshold=0.3,
        )
        self.assertTrue(details["matched"])
        self.assertEqual(details["matched_by"], "raw")
        self.assertAlmostEqual(details["raw_iou"], 7 / 9)
        self.assertEqual(details["raw_overlap_ratio"], 1.0)

    def test_boundary_tolerance_match_success(self) -> None:
        pred = {"start": 8.2, "end": 9.1}
        semantic = {"start": 9, "end": 15}

        no_tolerance = _temporal_match_details(
            pred,
            semantic,
            iou_threshold=0.1,
            overlap_ratio_threshold=0.3,
            boundary_tolerance_seconds=0.0,
        )
        self.assertFalse(no_tolerance["matched"])
        self.assertEqual(no_tolerance["matched_by"], "none")

        with_tolerance = _temporal_match_details(
            pred,
            semantic,
            iou_threshold=0.1,
            overlap_ratio_threshold=0.3,
            boundary_tolerance_seconds=1.0,
        )
        self.assertTrue(with_tolerance["matched"])
        self.assertEqual(with_tolerance["matched_by"], "boundary_tolerance")

    def test_far_segment_still_no_match(self) -> None:
        details = _temporal_match_details(
            {"start": 20, "end": 25},
            {"start": 9, "end": 15},
            iou_threshold=0.1,
            overlap_ratio_threshold=0.3,
        )
        self.assertFalse(details["matched"])
        self.assertEqual(details["matched_by"], "none")

    def test_tolerance_does_not_pollute_raw_metrics(self) -> None:
        details = _temporal_match_details(
            {"start": 8.2, "end": 9.1},
            {"start": 9, "end": 15},
            iou_threshold=0.1,
            overlap_ratio_threshold=0.3,
            boundary_tolerance_seconds=1.0,
        )
        self.assertAlmostEqual(details["raw_overlap_duration"], 0.1)
        self.assertAlmostEqual(details["tolerant_overlap_duration"], 0.9)
        self.assertLess(details["raw_iou"], details["tolerant_iou"])
        self.assertLess(details["raw_overlap_ratio"], details["tolerant_overlap_ratio"])

    def test_negative_tolerance_errors(self) -> None:
        with self.assertRaises(ValueError):
            _expand_segment_for_tolerance({"start": 9, "end": 15}, -1)

    def test_generic_precision_recall_f1(self) -> None:
        semantic_segments = [
            {
                "segment_id": "seg_001",
                "start": 0,
                "end": 9,
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
            {
                "segment_id": "seg_002",
                "start": 39,
                "end": 46,
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
            {
                "segment_id": "seg_003",
                "start": 100,
                "end": 110,
                "default_highlight_score": 5,
                "avoid_by_default": False,
            },
        ]
        result = compute_default_highlight_metrics(
            [{"start": 1, "end": 8}, {"start": 150, "end": 160}],
            semantic_segments,
        )
        self.assertEqual(result["default_highlight_precision"], 0.5)
        self.assertEqual(result["default_highlight_recall"], 0.333)
        self.assertEqual(result["default_highlight_f1"], 0.4)

    def test_empty_predictions_with_default_highlights(self) -> None:
        result = compute_default_highlight_metrics(
            [],
            [
                {
                    "segment_id": "seg_001",
                    "start": 0,
                    "end": 9,
                    "default_highlight_score": 5,
                    "avoid_by_default": False,
                }
            ],
        )
        self.assertEqual(result["default_highlight_precision"], 0.0)
        self.assertEqual(result["default_highlight_recall"], 0.0)
        self.assertEqual(result["default_highlight_f1"], 0.0)

    def test_empty_default_highlights_keeps_compatibility(self) -> None:
        result = compute_default_highlight_metrics(
            [],
            [
                {
                    "segment_id": "seg_001",
                    "start": 0,
                    "end": 9,
                    "default_highlight_score": 1,
                    "avoid_by_default": True,
                }
            ],
        )
        self.assertEqual(result["default_highlight_precision"], 0.0)
        self.assertEqual(result["default_highlight_recall"], 1.0)
        self.assertEqual(result["default_highlight_f1"], 0.0)


if __name__ == "__main__":
    unittest.main()
