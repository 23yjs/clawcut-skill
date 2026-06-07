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
