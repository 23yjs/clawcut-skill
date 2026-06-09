from __future__ import annotations

from evaluation.run_resolver_semantics_eval import _compare_expected_field, build_summary


def test_resolver_expected_field_missing_is_mismatch() -> None:
    mismatch = _compare_expected_field("relevant_segment_ids", ["seg_001"], {})
    assert mismatch is not None
    assert "<missing>" in mismatch


def test_resolver_segment_lists_use_ordered_exact_match() -> None:
    mismatch = _compare_expected_field(
        "forbidden_segment_ids",
        ["seg_001", "seg_002"],
        {"forbidden_segment_ids": ["seg_002", "seg_001"]},
    )
    assert mismatch is not None
    assert "ordered list" in mismatch


def test_resolver_duration_constraint_compares_expected_keys_only() -> None:
    assert (
        _compare_expected_field(
            "duration_constraint",
            {"status": "resolved", "max_seconds": 12},
            {"duration_constraint": {"status": "resolved", "max_seconds": 12, "reason": "ok"}},
        )
        is None
    )
    mismatch = _compare_expected_field(
        "duration_constraint",
        {"status": "resolved", "max_seconds": 12},
        {"duration_constraint": {"status": "not_specified", "max_seconds": None}},
    )
    assert mismatch is not None
    assert "status" in mismatch


def test_resolver_summary_exposes_comparison_policy() -> None:
    summary = build_summary([], [])
    assert summary["comparison_policy"]["segment_id_lists"] == "ordered_exact_match"
