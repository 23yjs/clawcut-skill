from __future__ import annotations

from evaluation.resolver_prompts import RESOLVER_PROMPT_VERSION, RESOLVER_SYSTEM_PROMPT


def test_resolver_prompt_version() -> None:
    assert RESOLVER_PROMPT_VERSION == "resolver_v6_exclusive_conflict_priority"


def test_prompt_distinguishes_global_exclusive_from_forbidden_segments() -> None:
    assert "不要其他内容不等于明确禁止片段" in RESOLVER_SYSTEM_PROMPT
    assert "不得将所有非 relevant 片段自动填入 forbidden_segment_ids" in RESOLVER_SYSTEM_PROMPT


def test_prompt_forbids_auto_completing_forbidden_segments() -> None:
    assert "forbidden_segment_ids 只记录用户明确点名禁止的片段" in RESOLVER_SYSTEM_PROMPT
    assert "不得自动记录所有非 relevant 片段" in RESOLVER_SYSTEM_PROMPT


def test_prompt_contains_key_classification_examples() -> None:
    assert "重点展示商品使用效果" in RESOLVER_SYSTEM_PROMPT
    assert "只保留商品使用效果，不要其他内容" in RESOLVER_SYSTEM_PROMPT
    assert "保留商品使用效果，不要片尾广告" in RESOLVER_SYSTEM_PROMPT
    assert "只保留商品使用效果，不要片尾广告" in RESOLVER_SYSTEM_PROMPT
