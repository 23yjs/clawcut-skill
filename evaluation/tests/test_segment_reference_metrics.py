from __future__ import annotations

import unittest

from evaluation.metrics import compute_segment_reference_metrics


class SegmentReferenceMetricTests(unittest.TestCase):
    def test_relevant_hit(self) -> None:
        result = compute_segment_reference_metrics(
            [{"start": 1, "end": 8}],
            [{"segment_id": "seg_001", "start": 0, "end": 9}],
            ["seg_001"],
            [],
        )
        self.assertEqual(result["relevant_segment_precision"], 1.0)
        self.assertEqual(result["relevant_segment_recall"], 1.0)
        self.assertEqual(result["relevant_segment_f1"], 1.0)

    def test_extra_irrelevant_prediction_lowers_precision(self) -> None:
        result = compute_segment_reference_metrics(
            [{"start": 1, "end": 8}, {"start": 150, "end": 160}],
            [{"segment_id": "seg_001", "start": 0, "end": 9}],
            ["seg_001"],
            [],
        )
        self.assertEqual(result["relevant_segment_precision"], 0.5)
        self.assertEqual(result["relevant_segment_recall"], 1.0)
        self.assertEqual(result["relevant_segment_f1"], 0.667)

    def test_forbidden_hit(self) -> None:
        result = compute_segment_reference_metrics(
            [{"start": 101, "end": 109}],
            [{"segment_id": "seg_003", "start": 100, "end": 110}],
            [],
            ["seg_003"],
        )
        self.assertEqual(result["forbidden_segment_hit_count"], 1)
        self.assertEqual(result["forbidden_segment_violation_rate"], 1.0)

    def test_empty_predictions(self) -> None:
        result = compute_segment_reference_metrics(
            [],
            [{"segment_id": "seg_001", "start": 0, "end": 9}],
            ["seg_001"],
            [],
        )
        self.assertEqual(result["relevant_segment_precision"], 0.0)
        self.assertEqual(result["relevant_segment_recall"], 0.0)
        self.assertEqual(result["relevant_segment_f1"], 0.0)
        self.assertTrue(result["warnings"])


if __name__ == "__main__":
    unittest.main()
