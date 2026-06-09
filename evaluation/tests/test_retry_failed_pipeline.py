from __future__ import annotations

import json
from pathlib import Path

from evaluation import retry_failed_pipeline as retry


def _case(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "video_id": case_id.replace("case_", "video_"),
        "video_filename": f"{case_id}.MP4",
        "input_video": f"/home/node/.openclaw/workspace/data/input/{case_id}.MP4",
        "skill_output_dir": f"/home/node/.openclaw/workspace/outputs/openclaw_collection_v2/{case_id}/{case_id}/run_01",
        "instruction": "帮我剪辑一下这个视频",
        "target_duration": None,
        "llm_video_url": f"https://example.com/{case_id}.MP4",
        "test_type": "baseline_generic",
        "priority": "baseline",
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _write_summary(output_dir: Path) -> None:
    payload = {
        "case_index": [
            {
                "case_id": "case_success",
                "video_id": "video_success",
                "official_eligible": True,
                "recommended_action": "none",
            },
            {
                "case_id": "case_rerun",
                "video_id": "video_rerun",
                "official_eligible": False,
                "recommended_action": "rerun_skill",
            },
            {
                "case_id": "case_judge",
                "video_id": "video_judge",
                "official_eligible": False,
                "recommended_action": "retry_judge",
            },
            {
                "case_id": "case_manual_required",
                "video_id": "video_manual_required",
                "official_eligible": False,
                "recommended_action": "manual_review_required",
                "manual_review_reasons": "人工核查",
            },
            {
                "case_id": "case_manual_recommended",
                "video_id": "video_manual_recommended",
                "official_eligible": True,
                "recommended_action": "manual_review_recommended",
                "manual_review_reasons": "内容选择得分低于 60",
            },
        ]
    }
    output_dir.mkdir(parents=True)
    (output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    output_dir = tmp_path / "official_v2"
    _write_summary(output_dir)
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            _case("case_success"),
            _case("case_rerun"),
            _case("case_judge"),
            _case("case_manual_required"),
            _case("case_manual_recommended"),
        ],
    )
    return output_dir, cases_path


def test_plan_routes_rerun_skill_and_retry_judge_separately(tmp_path):
    output_dir, cases_path = _fixture(tmp_path)

    plan = retry.build_plan(output_dir, cases_path)

    rerun_rows = retry.read_jsonl(plan.plan_dir / "rerun_skill_cases.jsonl")
    retry_eval_rows = retry.read_jsonl(plan.plan_dir / "retry_eval_cases.jsonl")
    assert [row["case_id"] for row in rerun_rows] == ["case_rerun"]
    assert "case_judge" not in [row["case_id"] for row in rerun_rows]
    assert {row["case_id"] for row in retry_eval_rows} == {"case_rerun", "case_judge"}
    assert plan.plan["skipped_success_case_ids"] == ["case_success"]


def test_manual_review_csvs_are_written(tmp_path):
    output_dir, cases_path = _fixture(tmp_path)

    plan = retry.build_plan(output_dir, cases_path)

    required = (plan.plan_dir / "manual_review_required.csv").read_text(encoding="utf-8")
    recommended = (plan.plan_dir / "manual_review_recommended.csv").read_text(encoding="utf-8")
    assert "case_manual_required" in required
    assert "case_manual_recommended" in recommended


def test_plan_only_does_not_execute_subprocess(tmp_path, monkeypatch):
    output_dir, cases_path = _fixture(tmp_path)
    calls = []
    monkeypatch.setattr(retry.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    retry.main(["--output-dir", str(output_dir), "--cases", str(cases_path), "--plan-only"])

    assert calls == []


def test_dry_run_does_not_execute_external_calls(tmp_path, monkeypatch):
    output_dir, cases_path = _fixture(tmp_path)
    calls = []
    monkeypatch.setattr(retry.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    retry.main([
        "--output-dir",
        str(output_dir),
        "--cases",
        str(cases_path),
        "--run-skill-reruns",
        "--run-eval-retries",
        "--dry-run",
    ])

    assert calls == []


def test_execution_flags_call_existing_scripts(tmp_path, monkeypatch):
    output_dir, cases_path = _fixture(tmp_path)
    calls = []

    def fake_run(command, check):
        calls.append(command)

    monkeypatch.setattr(retry.subprocess, "run", fake_run)

    retry.main([
        "--output-dir",
        str(output_dir),
        "--cases",
        str(cases_path),
        "--run-skill-reruns",
        "--run-eval-retries",
        "--resume",
        "--max-attempts",
        "3",
    ])

    assert any("evaluation/batch_dispatch_openclaw_official.py" in command for command in calls)
    assert any("evaluation.run_official_eval_report_v2" in command for command in calls)
    assert any("--max-attempts" in command and "3" in command for command in calls)


def test_repeated_plan_generation_does_not_overwrite_old_plan(tmp_path):
    output_dir, cases_path = _fixture(tmp_path)

    first = retry.build_plan(output_dir, cases_path)
    second = retry.build_plan(output_dir, cases_path)

    assert first.plan_dir != second.plan_dir
    assert (first.plan_dir / "retry_plan.json").exists()
    assert (second.plan_dir / "retry_plan.json").exists()
