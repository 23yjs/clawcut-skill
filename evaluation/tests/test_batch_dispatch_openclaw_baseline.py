from __future__ import annotations

import json
import subprocess
from pathlib import Path

from evaluation import batch_dispatch_openclaw_baseline as dispatch


def _case(tmp_path: Path, *, case_id: str = "generic__demo", url: str = "https://example.com/demo.MP4"):
    video = tmp_path / "demo.MP4"
    video.write_bytes(b"video")
    return dispatch.BaselineCase(
        case_id=case_id,
        video_id="demo",
        video_filename="demo.MP4",
        instruction=dispatch.BASELINE_INSTRUCTION,
        target_duration=None,
        local_input=str(video),
        llm_video_url=url,
    )


def _write_success_artifacts(run_dir: Path, *, fallback: bool = False, backend: str = "ark") -> None:
    reports = run_dir / "demo" / "reports"
    videos = run_dir / "demo" / "videos"
    logs = run_dir / "demo" / "logs"
    reports.mkdir(parents=True)
    videos.mkdir(parents=True)
    logs.mkdir(parents=True)
    (reports / "result_summary.json").write_text(
        json.dumps(
            {
                "status": "success",
                "skill_backend_requested": "ark",
                "skill_backend_used": backend,
                "fallback_used": fallback,
                "skill_instruction_effective": dispatch.BASELINE_INSTRUCTION,
                "user_instruction_original": f"{dispatch.BASELINE_INSTRUCTION} /input/demo.MP4",
                "model_interpreted_intent": "默认高光剪辑",
                "skill_llm_model": "ep-test",
                "skill_llm_prompt_tokens": 100,
                "skill_llm_completion_tokens": 25,
                "skill_llm_total_tokens": 125,
                "skill_llm_latency_seconds": 3.5,
                "skill_llm_video_source": "url",
                "skill_llm_request_started_at": "2026-06-04T00:00:00+00:00",
                "skill_llm_request_finished_at": "2026-06-04T00:00:03.5+00:00",
                "run_log": str(logs / "run.log"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (videos / "highlight.mp4").write_bytes(b"highlight")
    (logs / "run.log").write_text("ok", encoding="utf-8")


def test_duplicate_case_id_rejected(tmp_path):
    case = _case(tmp_path)
    try:
        dispatch.validate_cases([case, case])
    except dispatch.BatchDispatchError:
        return
    raise AssertionError("duplicate case_id should be rejected")


def test_dry_run_fails_when_local_video_missing(tmp_path, monkeypatch):
    case = _case(tmp_path)
    missing = dispatch.BaselineCase(
        **{**case.__dict__, "local_input": str(tmp_path / "missing.MP4")}
    )
    monkeypatch.setattr(dispatch.shutil, "which", lambda _: "/bin/openclaw")
    report = dispatch.dry_run(
        cases=[missing],
        output_root=tmp_path / "out",
        agent="main",
        timeout_seconds=1800,
        openclaw_checker=lambda _: subprocess.CompletedProcess([], 0, "", ""),
    )
    assert report["status"] == "failed"
    assert any("local_input not found" in error for error in report["errors"])


def test_dry_run_fails_when_llm_video_url_missing(tmp_path, monkeypatch):
    case = _case(tmp_path, url="")
    monkeypatch.setattr(dispatch.shutil, "which", lambda _: "/bin/openclaw")
    report = dispatch.dry_run(
        cases=[case],
        output_root=tmp_path / "out",
        agent="main",
        timeout_seconds=1800,
        openclaw_checker=lambda _: subprocess.CompletedProcess([], 0, "", ""),
    )
    assert report["status"] == "failed"
    assert any("llm_video_url is empty" in error for error in report["errors"])


def test_message_uses_skill_and_preserves_instruction(tmp_path):
    case = _case(tmp_path)
    message = dispatch.render_message(case, tmp_path / "out", "run_01")
    assert message.startswith("/skill clawcut-video-highlight")
    assert f"instruction:\n{dispatch.BASELINE_INSTRUCTION}" in message
    assert "target_duration:\n未指定" in message


def test_script_does_not_directly_call_skill_entrypoint():
    source = Path(dispatch.__file__).read_text(encoding="utf-8")
    assert "run_skill.py" not in source
    assert "openclaw" in source


def test_session_key_is_unique_per_case_and_run():
    first = dispatch.session_key_for("generic__a", "run_01")
    second = dispatch.session_key_for("generic__b", "run_01")
    third = dispatch.session_key_for("generic__a", "run_02")
    assert len({first, second, third}) == 3


def test_openclaw_command_uses_session_id_flag():
    command = dispatch.build_openclaw_command(
        agent="main",
        session_key="clawcut-baseline-generic__demo-run_01",
        message="hello",
        timeout_seconds=1800,
    )
    assert "--session-id" in command
    assert "--session-key" not in command


def test_choose_next_attempt_allocates_run_nn(tmp_path):
    case = _case(tmp_path)
    root = dispatch.case_root(tmp_path / "out", case)
    (root / "run_01").mkdir(parents=True)
    selection = dispatch.choose_next_attempt(tmp_path / "out", case, max_attempts=3, resume=True)
    assert selection.run_id == "run_02"
    assert selection.skipped is False


def test_find_attempt_artifacts_recursively_finds_unique_paths(tmp_path):
    run_dir = tmp_path / "run_01"
    _write_success_artifacts(run_dir)
    result = dispatch.find_attempt_artifacts(run_dir)
    assert result.status == "ready"
    assert result.result_summary_path and result.result_summary_path.name == "result_summary.json"
    assert result.highlight_video_path and result.highlight_video_path.name == "highlight.mp4"


def test_find_attempt_artifacts_detects_ambiguous_output(tmp_path):
    run_dir = tmp_path / "run_01"
    _write_success_artifacts(run_dir / "a")
    _write_success_artifacts(run_dir / "b")
    result = dispatch.find_attempt_artifacts(run_dir)
    assert result.status == "ambiguous_output"


def test_official_success_classification(tmp_path):
    run_dir = tmp_path / "run_01"
    _write_success_artifacts(run_dir)
    artifacts = dispatch.find_attempt_artifacts(run_dir)
    summary = dispatch.read_json_if_present(artifacts.result_summary_path)
    status, error = dispatch.classify_attempt(
        openclaw_exit_code=0,
        stdout_payload={"meta": {"transport": "gateway"}},
        artifact_search=artifacts,
        result_summary=summary,
    )
    assert status == "official_success"
    assert error is None


def test_diagnostic_skill_fallback_classification(tmp_path):
    run_dir = tmp_path / "run_01"
    _write_success_artifacts(run_dir, fallback=True, backend="mock")
    artifacts = dispatch.find_attempt_artifacts(run_dir)
    summary = dispatch.read_json_if_present(artifacts.result_summary_path)
    status, _ = dispatch.classify_attempt(
        openclaw_exit_code=0,
        stdout_payload={"meta": {"transport": "gateway"}},
        artifact_search=artifacts,
        result_summary=summary,
    )
    assert status == "diagnostic_skill_fallback"


def test_diagnostic_openclaw_fallback_classification(tmp_path):
    artifacts = dispatch.ArtifactSearchResult("failed", None, None, "missing")
    status, reason = dispatch.classify_attempt(
        openclaw_exit_code=0,
        stdout_payload={"meta": {"transport": "embedded", "fallbackFrom": "gateway"}},
        artifact_search=artifacts,
        result_summary={},
    )
    assert status == "diagnostic_openclaw_fallback"
    assert reason == "gateway"


def test_resume_does_not_overwrite_existing_directory(tmp_path):
    case = _case(tmp_path)
    root = dispatch.case_root(tmp_path / "out", case)
    run_01 = root / "run_01"
    run_01.mkdir(parents=True)
    (run_01 / "attempt_manifest.json").write_text(
        json.dumps({"collection_status": "diagnostic_skill_fallback"}),
        encoding="utf-8",
    )
    selection = dispatch.choose_next_attempt(tmp_path / "out", case, max_attempts=2, resume=True)
    assert selection.run_id == "run_02"
    assert not (root / "run_02").exists()


def test_official_success_is_skipped_on_resume(tmp_path):
    case = _case(tmp_path)
    root = dispatch.case_root(tmp_path / "out", case)
    run_01 = root / "run_01"
    run_01.mkdir(parents=True)
    (run_01 / "attempt_manifest.json").write_text(
        json.dumps({"collection_status": "official_success"}),
        encoding="utf-8",
    )
    selection = dispatch.choose_next_attempt(tmp_path / "out", case, max_attempts=2, resume=True)
    assert selection.skipped is True
    assert selection.run_id is None


def test_run_openclaw_attempt_writes_manifest_and_summaries(tmp_path):
    case = _case(tmp_path)
    output_root = tmp_path / "out"

    def fake_runner(command, **kwargs):
        assert command[0:2] == ["openclaw", "agent"]
        run_dir = dispatch.run_dir_for(output_root, case, "run_01")
        _write_success_artifacts(run_dir)
        return subprocess.CompletedProcess(command, 0, json.dumps({"meta": {"transport": "gateway"}}), "")

    manifest = dispatch.run_openclaw_attempt(
        case=case,
        output_root=output_root,
        agent="main",
        run_id="run_01",
        timeout_seconds=1800,
        command_runner=fake_runner,
    )
    dispatch.write_batch_outputs(output_root, [manifest])
    assert manifest["collection_status"] == "official_success"
    assert manifest["skill_llm_total_tokens"] == 125
    assert (output_root / "batch_progress.json").exists()
    csv_text = (output_root / "batch_results.csv").read_text(encoding="utf-8")
    assert "skill_llm_total_tokens" in csv_text
    assert "125" in csv_text
    assert (output_root / "batch_results.jsonl").exists()
