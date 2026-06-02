from __future__ import annotations

import unittest

from evaluation.instruction_resolver import ResolverValidationError, validate_resolver_result


GT = {
    "semantic_segments": [
        {"segment_id": "seg_001"},
        {"segment_id": "seg_002"},
        {"segment_id": "seg_003"},
    ]
}


def _result(**overrides):
    result = {
        "instruction_mode": "specific",
        "selection_scope": "preferential",
        "resolution_status": "resolved",
        "use_default_highlights": False,
        "relevant_segment_ids": ["seg_001"],
        "forbidden_segment_ids": [],
        "unresolved_requirements": [],
        "resolver_reason": "命中测试片段。",
    }
    result.update(overrides)
    return result


class InstructionResolverValidationTests(unittest.TestCase):
    def test_valid_generic(self) -> None:
        result = validate_resolver_result(
            _result(
                instruction_mode="generic",
                selection_scope="not_applicable",
                use_default_highlights=True,
                relevant_segment_ids=[],
                forbidden_segment_ids=[],
            ),
            GT,
        )
        self.assertTrue(result["use_default_highlights"])

    def test_valid_specific(self) -> None:
        result = validate_resolver_result(_result(), GT)
        self.assertEqual(result["instruction_mode"], "specific")
        self.assertEqual(result["selection_scope"], "preferential")

    def test_valid_specific_exclusive(self) -> None:
        result = validate_resolver_result(_result(selection_scope="exclusive"), GT)
        self.assertEqual(result["selection_scope"], "exclusive")

    def test_valid_conflict(self) -> None:
        result = validate_resolver_result(
            _result(instruction_mode="conflict", selection_scope="exclusive", forbidden_segment_ids=["seg_003"]),
            GT,
        )
        self.assertEqual(result["forbidden_segment_ids"], ["seg_003"])

    def test_valid_unresolved(self) -> None:
        result = validate_resolver_result(
            _result(
                instruction_mode="unresolved",
                selection_scope="unknown",
                resolution_status="unresolved",
                relevant_segment_ids=[],
                unresolved_requirements=["GT 没有情绪描述"],
            ),
            GT,
        )
        self.assertEqual(result["resolution_status"], "unresolved")

    def test_unknown_segment_id_errors(self) -> None:
        with self.assertRaises(ResolverValidationError):
            validate_resolver_result(_result(relevant_segment_ids=["seg_x"]), GT)

    def test_duplicate_relevant_errors(self) -> None:
        with self.assertRaises(ResolverValidationError):
            validate_resolver_result(_result(relevant_segment_ids=["seg_001", "seg_001"]), GT)

    def test_same_id_in_relevant_and_forbidden_errors(self) -> None:
        with self.assertRaises(ResolverValidationError):
            validate_resolver_result(_result(forbidden_segment_ids=["seg_001"]), GT)

    def test_generic_use_default_false_errors(self) -> None:
        with self.assertRaises(ResolverValidationError):
            validate_resolver_result(
                _result(
                    instruction_mode="generic",
                    selection_scope="not_applicable",
                    use_default_highlights=False,
                    relevant_segment_ids=[],
                    forbidden_segment_ids=[],
                ),
                GT,
            )

    def test_specific_resolved_without_relevant_errors(self) -> None:
        with self.assertRaises(ResolverValidationError):
            validate_resolver_result(_result(relevant_segment_ids=[]), GT)

    def test_specific_invalid_scope_errors(self) -> None:
        with self.assertRaises(ResolverValidationError):
            validate_resolver_result(_result(selection_scope="not_applicable"), GT)

    def test_conflict_with_explicit_forbidden_is_valid(self) -> None:
        result = validate_resolver_result(
            _result(
                instruction_mode="conflict",
                selection_scope="preferential",
                relevant_segment_ids=["seg_001"],
                forbidden_segment_ids=["seg_003"],
            ),
            GT,
        )
        self.assertEqual(result["selection_scope"], "preferential")

    def test_duration_constraint_max_bound_is_valid(self) -> None:
        result = validate_resolver_result(
            _result(
                duration_constraint={
                    "status": "resolved",
                    "min_seconds": 0,
                    "max_seconds": 30,
                    "source": "instruction",
                    "reason": "用户要求不超过 30 秒。",
                }
            ),
            GT,
        )
        self.assertEqual(result["duration_constraint"]["max_seconds"], 30)

    def test_duration_constraint_min_bound_is_valid(self) -> None:
        result = validate_resolver_result(
            _result(
                duration_constraint={
                    "status": "resolved",
                    "min_seconds": 30,
                    "max_seconds": None,
                    "source": "instruction",
                    "reason": "用户要求至少 30 秒。",
                }
            ),
            GT,
        )
        self.assertEqual(result["duration_constraint"]["min_seconds"], 30)

    def test_duration_constraint_range_is_valid(self) -> None:
        result = validate_resolver_result(
            _result(
                duration_constraint={
                    "status": "resolved",
                    "min_seconds": 30,
                    "max_seconds": 45,
                    "source": "instruction",
                    "reason": "用户要求 30–45 秒。",
                }
            ),
            GT,
        )
        self.assertEqual(result["duration_constraint"]["min_seconds"], 30)
        self.assertEqual(result["duration_constraint"]["max_seconds"], 45)

    def test_duration_constraint_not_specified_is_valid(self) -> None:
        result = validate_resolver_result(
            _result(
                duration_constraint={
                    "status": "not_specified",
                    "min_seconds": None,
                    "max_seconds": None,
                    "source": "none",
                    "reason": "未指定时长。",
                }
            ),
            GT,
        )
        self.assertEqual(result["duration_constraint"]["status"], "not_specified")

    def test_duration_constraint_unresolved_is_valid(self) -> None:
        result = validate_resolver_result(
            _result(
                duration_constraint={
                    "status": "unresolved",
                    "min_seconds": None,
                    "max_seconds": None,
                    "source": "instruction",
                    "reason": "用户仅要求尽量短，无法量化。",
                }
            ),
            GT,
        )
        self.assertEqual(result["duration_constraint"]["status"], "unresolved")

    def test_duration_constraint_reversed_bounds_errors(self) -> None:
        with self.assertRaises(ResolverValidationError):
            validate_resolver_result(
                _result(
                    duration_constraint={
                        "status": "resolved",
                        "min_seconds": 45,
                        "max_seconds": 30,
                        "source": "instruction",
                        "reason": "非法区间。",
                    }
                ),
                GT,
            )

    def test_duration_constraint_resolved_without_bounds_errors(self) -> None:
        with self.assertRaises(ResolverValidationError):
            validate_resolver_result(
                _result(
                    duration_constraint={
                        "status": "resolved",
                        "min_seconds": None,
                        "max_seconds": None,
                        "source": "instruction",
                        "reason": "非法。",
                    }
                ),
                GT,
            )

    def test_legacy_result_without_duration_constraint_gets_default(self) -> None:
        result = validate_resolver_result(_result(), GT)
        self.assertEqual(
            result["duration_constraint"],
            {
                "status": "not_specified",
                "min_seconds": None,
                "max_seconds": None,
                "source": "none",
                "reason": "legacy generated_case 未包含 duration_constraint。",
            },
        )


if __name__ == "__main__":
    unittest.main()
