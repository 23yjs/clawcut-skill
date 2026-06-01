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
            "artifact_validation": {"artifact_validation_passed": True, "skill_backend_used": "ark", "fallback_used": False},
            "technical_quality": {"technical_quality_passed": True},
            "duration_context": {},
            "time_metrics": {},
            "aesthetic_judge": {},
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
    summary = json.loads((tmp_path / "batch" / "summary.json").read_text(encoding="utf-8"))
    assert summary["failure_count"] == 1
