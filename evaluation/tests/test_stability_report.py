from __future__ import annotations

from evaluation.stability_report import load_cost_model, summarize_stability


def test_summarize_stability_groups_attempts_and_costs(tmp_path) -> None:
    cost_model = {
        "prompt_token_usd_per_1k": 0.1,
        "completion_token_usd_per_1k": 0.2,
    }
    summary = summarize_stability(
        [
            {
                "case_id": "case_a",
                "collection_status": "official_success",
                "skill_llm_prompt_tokens": 1000,
                "skill_llm_completion_tokens": 500,
                "skill_llm_total_tokens": 1500,
                "skill_llm_latency_seconds": 10,
                "selection_score_v1": 80,
                "highlight_video": "a1.mp4",
            },
            {
                "case_id": "case_a",
                "collection_status": "diagnostic_skill_fallback",
                "fallback_used": True,
                "skill_llm_prompt_tokens": 1000,
                "skill_llm_completion_tokens": 500,
                "skill_llm_total_tokens": 1500,
                "skill_llm_latency_seconds": 20,
                "selection_score_v1": 70,
                "highlight_video": "a2.mp4",
            },
        ],
        cost_model,
    )
    case = summary["cases"][0]
    assert case["attempt_count"] == 2
    assert case["official_success_rate"] == 0.5
    assert case["skill_fallback_rate"] == 0.5
    assert case["avg_latency_seconds"] == 15
    assert case["avg_skill_llm_total_tokens"] == 1500
    assert case["estimated_cost"] == 0.4
    assert case["selection_score_std"] == 5
    assert case["final_segments_changed"] is True
    assert len(case["attempts"]) == 2
    assert case["attempts"][0]["status"] == "official_success"
    assert case["attempts"][1]["skill_fallback"] is True


def test_load_cost_model_parses_simple_yaml(tmp_path) -> None:
    path = tmp_path / "cost.yaml"
    path.write_text("prompt_token_usd_per_1k: 0.1\ncompletion_token_usd_per_1k: 0.2\n", encoding="utf-8")
    assert load_cost_model(path) == {
        "prompt_token_usd_per_1k": 0.1,
        "completion_token_usd_per_1k": 0.2,
    }
