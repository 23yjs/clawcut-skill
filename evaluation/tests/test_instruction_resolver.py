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

    def test_valid_conflict(self) -> None:
        result = validate_resolver_result(
            _result(instruction_mode="conflict", forbidden_segment_ids=["seg_003"]),
            GT,
        )
        self.assertEqual(result["forbidden_segment_ids"], ["seg_003"])

    def test_valid_unresolved(self) -> None:
        result = validate_resolver_result(
            _result(
                instruction_mode="unresolved",
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
                    use_default_highlights=False,
                    relevant_segment_ids=[],
                    forbidden_segment_ids=[],
                ),
                GT,
            )

    def test_specific_resolved_without_relevant_errors(self) -> None:
        with self.assertRaises(ResolverValidationError):
            validate_resolver_result(_result(relevant_segment_ids=[]), GT)


if __name__ == "__main__":
    unittest.main()
