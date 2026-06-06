from __future__ import annotations

import json
from pathlib import Path

from evaluation import run_batch_eval


def test_batch_continues_after_case_failure(tmp_path, monkeypatch):
    cases_path = tmp_path / "cases.jsonl"
    cases = [
        {
            "case_id": "ok",
            "input_video": "data/input/demo1.MP4",
            "instruction": "剪出高光",
            "skill_output_dir": "outputs/demo1",
            "judge_video_url": "https://example.com/a.mp4",
        },
        {
            "case_id": "bad",
            "input_video": "data/input/demo2.MP4",
            "instruction": "剪出高光",
            "skill_output_dir": "outputs/demo2",
        },
    ]
    cases_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in cases), encoding="utf-8")

    def fake_run_auto_eval(config):
        if config.output_dir.name == "bad":
            raise RuntimeError("boom")
        assert config.judge_video_url == "https://example.com/a.mp4"
        assert config.eval_run_id == "batch"
        assert config.case_id == "ok"
        config.output_dir.mkdir(parents=True, exist_ok=True)
        return {
            "evaluation_status": "scored_complete",
            "evaluation_scope": "official",
            "video_id": "demo1",
            "instruction_mode": "generic",
            "selection_scope": "not_applicable",
            "selection_score_v1": 80,
            "aesthetic_score_v1": 90,
            "final_score_v2": 83,
            "artifact_validation": {
                "artifact_validation_passed": True,
                "skill_backend_used": "ark",
                "fallback_used": False,
                "result_summary": {"skill_llm_total_tokens": 123, "skill_llm_latency_seconds": 4.5},
            },
            "technical_quality": {"technical_quality_passed": True},
            "duration_context": {},
            "time_metrics": {},
            "aesthetic_judge": {
                "judge_metadata": [
                    {
                        "aesthetic_judge_latency_seconds": 2.5,
                        "aesthetic_judge_usage": {
                            "prompt_tokens": 1000,
                            "completion_tokens": 200,
                            "total_tokens": 1200,
                        },
                    }
                ]
            },
            "resolver_metadata": {"resolver_latency_seconds": 1.2, "resolver_usage": {"total_tokens": 456}},
            "evaluation_elapsed_seconds": 7.5,
        }

    monkeypatch.setattr(run_batch_eval, "run_auto_eval", fake_run_auto_eval)
    code = run_batch_eval.main_from_args if hasattr(run_batch_eval, "main_from_args") else None
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_batch_eval.py",
            "--cases",
            str(cases_path),
            "--gt_dir",
            str(tmp_path / "gt"),
            "--output_dir",
            str(tmp_path / "batch"),
        ],
    )
    assert run_batch_eval.main() == 0
    csv_text = (tmp_path / "batch" / "results.csv").read_text(encoding="utf-8")
    assert "freeze_frame_ratio" in csv_text
    assert "dover_status" in csv_text
    assert "editing_experience_score_v1" in csv_text
    assert "skill_llm_total_tokens" in csv_text
    assert "resolver_total_tokens" in csv_text
    assert "aesthetic_judge_total_tokens" in csv_text
    assert "evaluation_total_tokens" in csv_text
    assert "pipeline_total_tokens" in csv_text
    summary = json.loads((tmp_path / "batch" / "summary.json").read_text(encoding="utf-8"))
    assert summary["failure_count"] == 1
    assert summary["total_aesthetic_judge_tokens"] == 1200.0
    assert summary["total_evaluation_tokens"] == 1656.0
    assert summary["total_pipeline_tokens"] == 1779.0
    assert (tmp_path / "batch" / "report.html").exists()
    assert (tmp_path / "batch" / "technical_appendix.html").exists()
    assert (tmp_path / "batch" / "cases" / "ok.html").exists()


def test_batch_uses_judge_url_map(tmp_path, monkeypatch):
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        json.dumps(
            {
                "case_id": "mapped",
                "input_video": "data/input/demo.MP4",
                "instruction": "剪出高光",
                "skill_output_dir": "outputs/demo/run_02",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    judge_map = tmp_path / "judge_urls.csv"
    judge_map.write_text("case_id,judge_video_url\nmapped,https://example.com/mapped.mp4\n", encoding="utf-8")

    def fake_run_auto_eval(config):
        assert config.judge_video_url == "https://example.com/mapped.mp4"
        assert config.skill_run_id == "run_02"
        return {
            "evaluation_status": "scored_complete",
            "evaluation_scope": "official",
            "video_id": "demo",
            "selection_score_v1": 90,
            "final_score_v2": 90,
            "artifact_validation": {"artifact_validation_passed": True},
            "technical_quality": {"technical_quality_passed": True},
            "duration_context": {},
            "time_metrics": {},
        }

    monkeypatch.setattr(run_batch_eval, "run_auto_eval", fake_run_auto_eval)
    assert run_batch_eval.main_from_args(
        [
            "--cases",
            str(cases_path),
            "--gt_dir",
            str(tmp_path / "gt"),
            "--output_dir",
            str(tmp_path / "batch"),
            "--judge-url-map",
            str(judge_map),
        ]
    ) == 0
