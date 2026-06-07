from __future__ import annotations

import json
import subprocess
from pathlib import Path

from evaluation import batch_dispatch_openclaw_official as dispatch


def _case(
    tmp_path: Path,
    *,
    case_id: str = "specific_excl__demo",
    target_duration=None,
    priority: str = "priority",
) -> dispatch.OfficialCase:
    video = tmp_path / "demo.MP4"
    video.write_bytes(b"video")
    return dispatch.OfficialCase(
        case_id=case_id,
        video_id="demo",
        video_filename="demo.MP4",
        input_video=str(video),
        skill_output_dir=str(tmp_path / "out" / "demo" / case_id / "run_01"),
        instruction="只保留商品使用效果，不要其他内容。",
        target_duration=target_duration,
        llm_video_url="https://example.com/demo.MP4",
        test_type="specific_following",
        priority=priority,
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
                "skill_instruction_effective": "只保留商品使用效果，不要其他内容。",
                "user_instruction_original": "只保留商品使用效果，不要其他内容。 /input/demo.MP4",
                "model_interpreted_intent": "指定内容剪辑",
                "skill_llm_model": "ep-test",
                "skill_llm_prompt_tokens": 100,
                "skill_llm_completion_tokens": 25,
                "skill_llm_total_tokens": 125,
                "skill_llm_latency_seconds": 3.5,
                "skill_llm_video_source": "url",
                "skill_llm_request_started_at": "2026-06-06T00:00:00+00:00",
                "skill_llm_request_finished_at": "2026-06-06T00:00:03.5+00:00",
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


def test_message_uses_skill_and_official_protocol(tmp_path):
    message = dispatch.render_message(_case(tmp_path), "run_01")
    assert message.startswith("/skill clawcut-video-highlight")
    assert "[CLAWCUT_OFFICIAL_COLLECTION_V1]" in message
    assert "instruction:\n只保留商品使用效果，不要其他内容。" in message


def test_default_path_map_translates_container_workspace(tmp_path):
    path_map = dispatch.apply_default_path_map({}, mac_workspace=tmp_path)
    mapped = dispatch.map_path(
        "/home/node/.openclaw/workspace/data/eval/cases.official.v2.jsonl",
        path_map,
    )
    assert mapped == tmp_path / "data" / "eval" / "cases.official.v2.jsonl"


def test_openclaw_command_uses_agent_and_session_id():
    command = dispatch.build_openclaw_command(
        agent="main",
        session_key="clawcut-official-specific_excl__demo-run_01",
        message="hello",
        timeout_seconds=1800,
    )
    assert command[:2] == ["openclaw", "agent"]
    assert "--session-id" in command
    assert "--session-key" not in command


def test_script_does_not_directly_call_skill_entrypoint_or_renderer():
    source = Path(dispatch.__file__).read_text(encoding="utf-8")
    assert "run_skill.py" not in source
    assert '"ffmpeg"' not in source
    assert "'ffmpeg'" not in source
    assert "openclaw" in source


def test_target_duration_null_message_forbids_target_arg(tmp_path):
    message = dispatch.render_message(_case(tmp_path, target_duration=None), "run_01")
    assert "target_duration:\n未指定" in message
    assert "不得传入 --target_duration" in message


def test_target_duration_number_message_requires_target_arg(tmp_path):
    message = dispatch.render_message(_case(tmp_path, target_duration=30), "run_01")
    assert "target_duration:\n30" in message
    assert "必须传入 --target_duration 30" in message


def test_session_key_is_unique_per_case_and_run():
    first = dispatch.session_key_for("case_a", "run_01")
    second = dispatch.session_key_for("case_b", "run_01")
    third = dispatch.session_key_for("case_a", "run_02")
    assert len({first, second, third}) == 3


def test_choose_next_attempt_allocates_new_run_without_overwrite(tmp_path):
    case = _case(tmp_path)
    Path(case.skill_output_dir).mkdir(parents=True)
    selection = dispatch.choose_next_attempt(case, max_attempts=3, resume=True)
    assert selection.run_id == "run_02"
    assert selection.skipped is False
    assert not (Path(case.skill_output_dir).parent / "run_02").exists()


def test_resume_skips_existing_official_success(tmp_path):
    case = _case(tmp_path)
    run_01 = Path(case.skill_output_dir)
    run_01.mkdir(parents=True)
    (run_01 / "attempt_manifest.json").write_text(
        json.dumps({"collection_status": "official_success"}),
        encoding="utf-8",
    )
    selection = dispatch.choose_next_attempt(case, max_attempts=2, resume=True)
    assert selection.skipped is True
    assert selection.run_id is None


def test_dry_run_fails_when_llm_video_url_missing(tmp_path, monkeypatch):
    case = dispatch.OfficialCase(
        **{**_case(tmp_path).__dict__, "llm_video_url": ""}
    )
    config = tmp_path / "default.yaml"
    config.write_text("llm:\n  fallback_to_mock: false\n", encoding="utf-8")
    monkeypatch.setattr(dispatch.shutil, "which", lambda _: "/bin/openclaw")
    report = dispatch.dry_run(
        cases=[case],
        agent="main",
        timeout_seconds=1800,
        skill_config=config,
        openclaw_checker=lambda _: subprocess.CompletedProcess([], 0, "", ""),
    )
    assert report["status"] == "failed"
    assert any("llm_video_url" in error for error in report["errors"])


def test_dry_run_checks_mock_disabled(tmp_path, monkeypatch):
    case = _case(tmp_path)
    gt_dir = tmp_path / "data" / "eval"
    gt_dir.mkdir(parents=True)
    (gt_dir / "demo.json").write_text("{}", encoding="utf-8")
    config = tmp_path / "default.yaml"
    config.write_text("llm:\n  fallback_to_mock: true\n", encoding="utf-8")
    monkeypatch.setattr(dispatch.shutil, "which", lambda _: "/bin/openclaw")
    report = dispatch.dry_run(
        cases=[case],
        agent="main",
        timeout_seconds=1800,
        path_map={"/home/node/.openclaw/workspace": str(tmp_path)},
        skill_config=config,
        openclaw_checker=lambda _: subprocess.CompletedProcess([], 0, "", ""),
    )
    assert report["status"] == "failed"
    assert any("fallback_to_mock" in error for error in report["errors"])


def test_run_openclaw_attempt_writes_manifest(tmp_path):
    case = _case(tmp_path)

    def fake_runner(command, **kwargs):
        assert command[:2] == ["openclaw", "agent"]
        _write_success_artifacts(Path(case.skill_output_dir))
        return subprocess.CompletedProcess(command, 0, json.dumps({"meta": {"transport": "gateway"}}), "")

    manifest = dispatch.run_openclaw_attempt(
        case=case,
        agent="main",
        run_id="run_01",
        timeout_seconds=1800,
        command_runner=fake_runner,
    )
    assert manifest["collection_status"] == "official_success"
    assert (Path(case.skill_output_dir) / "dispatch_message.txt").exists()
    assert (Path(case.skill_output_dir) / "attempt_manifest.json").exists()
