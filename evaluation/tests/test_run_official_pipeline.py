from __future__ import annotations

import json
from pathlib import Path

from evaluation import run_official_pipeline


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _artifact_tree(host_workspace: Path, video_id: str, *, backend: str = "ark", fallback: bool = False) -> Path:
    run_dir = host_workspace / "outputs" / "openclaw_collection_v1" / video_id / f"generic__{video_id}" / "run_01"
    skill_dir = run_dir / video_id
    container_skill_dir = f"/home/node/.openclaw/workspace/outputs/openclaw_collection_v1/{video_id}/generic__{video_id}/run_01/{video_id}"
    _write_json(
        skill_dir / "reports" / "result_summary.json",
        {
            "status": "success",
            "input_video": f"/home/node/.openclaw/workspace/data/input/{video_id}.MP4",
            "instruction": "帮我剪辑一下这个视频",
            "target_duration": None,
            "skill_backend_requested": "ark",
            "skill_backend_used": backend,
            "fallback_used": fallback,
            "segments_json": f"{container_skill_dir}/reports/segments.json",
            "highlight_video": f"{container_skill_dir}/videos/highlight.mp4",
            "run_log": f"{container_skill_dir}/logs/run.log",
        },
    )
    _write_json(skill_dir / "reports" / "segments.json", {"final_segments": [{"start": 0, "end": 1}]})
    (skill_dir / "videos").mkdir(parents=True)
    (skill_dir / "videos" / "highlight.mp4").write_bytes(b"video")
    (skill_dir / "logs").mkdir(parents=True)
    (skill_dir / "logs" / "run.log").write_text("使用的 LLM backend：ark\n", encoding="utf-8")
    _write_json(
        run_dir / "attempt_manifest.json",
        {
            "case_id": f"generic__{video_id}",
            "video_id": video_id,
            "run_id": "run_01",
            "collection_status": "diagnostic_openclaw_fallback",
            "openclaw_transport": "embedded",
            "skill_backend_used": backend,
            "fallback_used": fallback,
        },
    )
    return run_dir


def _case(video_id: str) -> dict:
    return {
        "case_id": f"generic__{video_id}",
        "video_id": video_id,
        "instruction": "帮我剪辑一下这个视频",
        "target_duration": None,
        "test_type": "baseline_generic",
        "priority": "baseline",
        "input_video": f"/home/node/.openclaw/workspace/data/input/{video_id}.MP4",
        "skill_output_dir": f"/home/node/.openclaw/workspace/outputs/openclaw_collection_v1/{video_id}/generic__{video_id}/run_01",
    }


def test_official_pipeline_partitions_cases_and_writes_manifest(tmp_path, monkeypatch):
    host_workspace = tmp_path / "workspace"
    (host_workspace / "data" / "input").mkdir(parents=True)
    for video_id in ["ready_demo", "fallback_demo", "missing_demo"]:
        (host_workspace / "data" / "input" / f"{video_id}.MP4").write_bytes(b"source")
    _artifact_tree(host_workspace, "ready_demo")
    _artifact_tree(host_workspace, "fallback_demo", backend="mock", fallback=True)

    cases = [_case("ready_demo"), _case("fallback_demo"), _case("missing_demo")]
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text("\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n", encoding="utf-8")

    def fake_batch(argv):
        output_dir = Path(argv[argv.index("--output_dir") + 1])
        run_dir = output_dir / "runs" / "generic__ready_demo"
        _write_json(run_dir / "evaluation_result.json", {"evaluation_status": "selection_scored_aesthetic_pending"})
        _write_json(run_dir / "tos_upload.json", {"upload_status": "failed", "object_key": "judge/generic__ready_demo.mp4"})
        return 0

    monkeypatch.setattr(run_official_pipeline, "run_batch_eval_main", fake_batch)
    output_dir = tmp_path / "official"
    summary = run_official_pipeline.run_pipeline(
        run_official_pipeline.parse_args(
            [
                "--cases",
                str(cases_path),
                "--output-dir",
                str(output_dir),
                "--path-map",
                f"/home/node/.openclaw/workspace={host_workspace}",
                "--tos_key_prefix",
                "judge",
            ]
        )
    )

    assert summary["ready_for_effect_eval"] == 1
    assert summary["diagnostic_case_count"] == 1
    assert summary["missing_case_count"] == 1
    manifest_rows = [
        json.loads(line)
        for line in (output_dir / "artifact_manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    ready = next(row for row in manifest_rows if row["case_id"] == "generic__ready_demo")
    fallback = next(row for row in manifest_rows if row["case_id"] == "generic__fallback_demo")
    assert ready["effect_eval_eligibility"] == "ready_for_effect_eval"
    assert ready["openclaw_transport"] == "embedded"
    assert ready["evaluation_status"] == "selection_scored_aesthetic_pending"
    assert fallback["effect_eval_eligibility"] == "diagnostic_skill_fallback"
    missing_rows = [
        json.loads(line)
        for line in (output_dir / "official_missing_cases.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    rerun_rows = [
        json.loads(line)
        for line in (output_dir / "rerun_openclaw_cases.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["video_id"] for row in missing_rows] == ["missing_demo"]
    assert "fallback_demo" in {row["video_id"] for row in rerun_rows}
    assert "generic__ready_demo" in (output_dir / "manual_upload_todo.csv").read_text(encoding="utf-8")
