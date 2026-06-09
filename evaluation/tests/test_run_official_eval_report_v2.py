from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from evaluation import run_official_eval_report_v2 as report_v2


def _case(tmp_path: Path) -> dict:
    return {
        "case_id": "generic__demo",
        "video_id": "demo",
        "input_video": str(tmp_path / "demo.MP4"),
        "skill_output_dir": str(tmp_path / "outputs" / "demo" / "generic__demo" / "run_01"),
        "instruction": "帮我剪辑一下这个视频",
        "target_duration": None,
        "test_type": "baseline_generic",
        "primary_capability": "默认剪辑能力",
        "tested_capability": "默认高光识别",
        "challenge_tags": ["baseline"],
        "execution_tier": "baseline",
        "include_in_official_score": True,
    }


def _base_result() -> dict:
    return {
        "evaluation_status": "scored_complete",
        "evaluation_scope": "official",
        "video_id": "demo",
        "instruction": "帮我剪辑一下这个视频",
        "selection_score_v1": 80,
        "editing_experience_score_v1": 70,
        "aesthetic_score_v1": 70,
        "final_score_v2": 77,
        "evaluation_elapsed_seconds": 4.0,
        "artifact_validation": {
            "artifact_validation_passed": True,
            "skill_backend_used": "ark",
            "fallback_used": False,
            "result_summary": {
                "skill_run_elapsed_seconds": 30.0,
                "preview_generation_seconds": None,
                "skill_llm_latency_seconds": 12.0,
                "ffmpeg_render_seconds": 3.0,
                "skill_llm_prompt_tokens": 100,
                "skill_llm_completion_tokens": 50,
                "skill_llm_total_tokens": 150,
                "skill_llm_attempt_count": 1,
            },
        },
        "technical_quality": {"technical_quality_passed": True},
        "resolver_metadata": {
            "resolver_latency_seconds": 2.0,
            "resolver_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
        "aesthetic_judge": {
            "judge_status": "scored",
            "judge_metadata": [
                {
                    "aesthetic_judge_latency_seconds": 3.0,
                    "aesthetic_judge_usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 10,
                        "total_tokens": 30,
                    },
                }
            ],
        },
    }


def test_report_only_builds_v2_summary_xlsx_and_static_html(tmp_path):
    cases = [_case(tmp_path)]
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(json.dumps(cases[0], ensure_ascii=False) + "\n", encoding="utf-8")
    run_dir = tmp_path / "out" / "runs" / "generic__demo"
    run_dir.mkdir(parents=True)
    (run_dir / "evaluation_result.json").write_text(
        json.dumps(_base_result(), ensure_ascii=False),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        mode="report-only",
        cases=cases_path,
        gt_dir=tmp_path,
        output_dir=tmp_path / "out",
        path_map=[],
        resume=False,
        retry_failed=False,
        technical_quality_config=Path("evaluation/config/default.yaml"),
        auto_upload_judge_video=False,
        tos_bucket=None,
        tos_region=None,
        tos_endpoint=None,
        tos_key_prefix=None,
        tos_presign_expires_seconds=None,
    )

    summary = report_v2.run(args)

    assert summary["summary_schema_version"] == "official_eval_summary_v2"
    assert "human_report" not in summary
    assert "legacy" not in summary
    assert summary["overall_scores"]["final_score_v2_avg"] == 77
    assert (tmp_path / "out" / "case_results.csv").exists()
    assert (tmp_path / "out" / "report.html").exists()
    assert "fetch(" not in (tmp_path / "out" / "report.html").read_text(encoding="utf-8")
    with zipfile.ZipFile(tmp_path / "out" / "case_results.xlsx") as archive:
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
    for sheet_name in report_v2.XLSX_SHEETS:
        assert sheet_name in workbook


def test_fallback_case_is_not_official_average(tmp_path):
    case = _case(tmp_path)
    result = _base_result()
    result["artifact_validation"]["fallback_used"] = True
    result["evaluation_status"] = "diagnostic_only"
    enriched = report_v2.enrich_result_v2(case, result)
    assert enriched["official_score_eligibility"]["eligible"] is False
    assert enriched["consumption"]["end_to_end"]["llm_total_tokens"] == 195


def test_missing_usage_stays_null_not_zero(tmp_path):
    case = _case(tmp_path)
    result = _base_result()
    result["artifact_validation"]["result_summary"].pop("skill_llm_total_tokens")
    result["resolver_metadata"]["resolver_usage"].pop("total_tokens")
    result["aesthetic_judge"]["judge_metadata"] = []
    enriched = report_v2.enrich_result_v2(case, result)
    assert enriched["consumption"]["video_editing"]["llm_total_tokens"] is None
    assert enriched["consumption"]["evaluation"]["llm_total_tokens"] is None
    assert enriched["consumption"]["end_to_end"]["llm_total_tokens"] is None


def test_low_selection_score_triggers_issue_and_manual_recommendation(tmp_path):
    case = _case(tmp_path)
    result = _base_result()
    result["selection_score_v1"] = 59.99
    result["time_metrics"] = {
        "relevant_duration_coverage": 0.425,
        "acceptable_precision": 0.68,
        "default_highlight_precision": 0.55,
        "forbidden_duration_ratio": 0.0,
        "avoid_by_default_overlap_ratio": 0.18,
    }
    result["duration_context"] = {"duration_score": 100.0}

    enriched = report_v2.enrich_result_v2(case, result)

    assert enriched["manual_review"]["recommended"] is True
    assert enriched["manual_review_reasons"]
    assert enriched["manual_review_reasons"] == enriched["manual_review"]["recommended_reasons"]
    assert "内容选择得分低于 60" in enriched["manual_review"]["recommended_reasons"]
    assert enriched["recommended_action"] == "manual_review_recommended"
    issue = [item for item in enriched["issue_summary"] if item["issue_type"] == "content_selection_issue"][0]
    assert "内容选择得分偏低：59.99 分，低于告警阈值 60 分" in issue["display_text"]
    assert "目标内容覆盖率：42.5%" in issue["display_text"]
    assert "时长约束得分：100.0%" in issue["display_text"]


def test_selection_score_equal_threshold_does_not_trigger_low_score_warning(tmp_path):
    case = _case(tmp_path)
    result = _base_result()
    result["selection_score_v1"] = 60

    enriched = report_v2.enrich_result_v2(case, result)

    assert enriched["manual_review"]["recommended"] is False
    assert not [item for item in enriched["issue_summary"] if item["issue_type"] == "content_selection_issue"]


def test_forbidden_overlap_recommends_manual_review(tmp_path):
    case = _case(tmp_path)
    result = _base_result()
    result["selection_score_v1"] = 80
    result["time_metrics"] = {"forbidden_duration_ratio": 0.01}

    enriched = report_v2.enrich_result_v2(case, result)

    assert enriched["manual_review"]["recommended"] is True
    assert "存在明确禁止内容混入，混入比例 1.0%" in enriched["manual_review"]["recommended_reasons"]
    assert enriched["recommended_action"] == "manual_review_recommended"
    assert "明确禁止内容混入比例 1.0%" in enriched["content_selection_attribution"]
    issue = [item for item in enriched["issue_summary"] if item["issue_type"] == "forbidden_content_issue"][0]
    assert issue["label"] == "混入用户明确禁止内容"


def test_invalid_artifact_enters_rerun_skill_not_manual_review(tmp_path):
    case = _case(tmp_path)
    result = _base_result()
    result["evaluation_status"] = "invalid_artifact"
    result["artifact_validation"]["artifact_validation_passed"] = False

    enriched = report_v2.enrich_result_v2(case, result)

    assert enriched["recommended_action"] == "rerun_skill"
    assert enriched["manual_review"]["required"] is False
    assert enriched["manual_review"]["recommended"] is False


def test_judge_failed_enters_retry_judge_not_manual_review(tmp_path):
    case = _case(tmp_path)
    result = _base_result()
    result["evaluation_status"] = "judge_failed"

    enriched = report_v2.enrich_result_v2(case, result)

    assert enriched["recommended_action"] == "retry_judge"
    assert enriched["manual_review"]["required"] is False
    assert enriched["manual_review"]["recommended"] is False


def test_manual_review_required_statuses_are_required(tmp_path):
    case = _case(tmp_path)
    for status in ["manual_review_required", "judge_manual_review_required"]:
        result = _base_result()
        result["evaluation_status"] = status

        enriched = report_v2.enrich_result_v2(case, result)

        assert enriched["manual_review"]["required"] is True
        assert enriched["recommended_action"] == "manual_review_required"


def test_report_only_reenriches_existing_v2_result(tmp_path):
    case = _case(tmp_path)
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    run_dir = tmp_path / "out" / "runs" / case["case_id"]
    run_dir.mkdir(parents=True)
    old_result = _base_result()
    old_result["evaluation_result_schema_version"] = report_v2.EVALUATION_RESULT_SCHEMA_VERSION
    old_result["selection_score_v1"] = 59.99
    old_result["manual_review"] = {"required": False, "recommended": False}
    (run_dir / "evaluation_result.json").write_text(
        json.dumps(old_result, ensure_ascii=False),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        mode="report-only",
        cases=cases_path,
        gt_dir=tmp_path,
        output_dir=tmp_path / "out",
        path_map=[],
        resume=False,
        retry_failed=False,
        technical_quality_config=Path("evaluation/config/default.yaml"),
        auto_upload_judge_video=False,
        tos_bucket=None,
        tos_region=None,
        tos_endpoint=None,
        tos_key_prefix=None,
        tos_presign_expires_seconds=None,
    )

    summary = report_v2.run(args)

    assert summary["manual_review_summary"]["recommended_count"] == 1
    html = (tmp_path / "out" / "report.html").read_text(encoding="utf-8")
    assert "待处理事项" in html
    assert "正式计分覆盖率" in html
    assert "人工抽查原因" in html
    assert "内容选择问题归因" in html
    assert "执行失败或未完成" not in html
    assert "产物无效，需重新运行 Skill" in html
    assert "有效成片技术质量通过率" in html


def test_official_case_challenge_tags_have_chinese_video_scenario_labels(tmp_path):
    cases = report_v2.read_jsonl(Path("data/eval/cases.official.v2.jsonl"))
    results = [report_v2.enrich_result_v2(case, _base_result()) for case in cases]

    rows = report_v2._video_scenario_breakdown(cases, results)

    assert rows
    for row in rows:
        assert row["label"] != row["key"]
        assert "_" not in row["label"]
