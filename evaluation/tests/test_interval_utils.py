from __future__ import annotations

import unittest

from evaluation.interval_utils import (
    intersect_intervals,
    intervals_duration,
    normalize_intervals,
    overlap_duration_between,
)


class IntervalUtilsTests(unittest.TestCase):
    def test_overlapping_predictions_are_merged(self) -> None:
        intervals = normalize_intervals([{"start": 0, "end": 10}, {"start": 8, "end": 15}])
        self.assertEqual(intervals, [{"start": 0.0, "end": 15.0}])
        self.assertEqual(intervals_duration(intervals), 15.0)

    def test_intersections(self) -> None:
        result = intersect_intervals(
            [{"start": 0, "end": 10}, {"start": 20, "end": 30}],
            [{"start": 5, "end": 25}],
        )
        self.assertEqual(result, [{"start": 5.0, "end": 10.0}, {"start": 20.0, "end": 25.0}])
        self.assertEqual(overlap_duration_between(result, [{"start": 0, "end": 100}]), 10.0)

    def test_invalid_interval_errors(self) -> None:
        with self.assertRaises(ValueError):
            normalize_intervals([{"start": 1, "end": 1}])


if __name__ == "__main__":
    unittest.main()
