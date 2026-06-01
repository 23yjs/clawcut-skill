from __future__ import annotations

import subprocess
from pathlib import Path

from evaluation.dover_quality import DoverConfig, evaluate_dover_quality


def test_dover_disabled_returns_disabled(tmp_path):
    result = evaluate_dover_quality(tmp_path / "highlight.mp4", DoverConfig(enabled=False))
    assert result["dover_status"] == "disabled"


def test_dover_unavailable_without_repo(tmp_path):
    result = evaluate_dover_quality(
        tmp_path / "highlight.mp4",
        DoverConfig(enabled=True, repo_dir=tmp_path / "missing", python="python"),
    )
    assert result["dover_status"] == "unavailable"


def test_dover_require_marks_required_failure(tmp_path):
    result = evaluate_dover_quality(
        tmp_path / "highlight.mp4",
        DoverConfig(enabled=True, require_dover=True, repo_dir=tmp_path / "missing", python="python"),
    )
    assert result["dover_status"] == "unavailable"
    assert result["dover_required_failed"] is True


def test_dover_timeout(tmp_path, monkeypatch):
    repo = tmp_path / "DOVER"
    repo.mkdir()

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = evaluate_dover_quality(
        tmp_path / "highlight.mp4",
        DoverConfig(enabled=True, repo_dir=repo, python="python", timeout_seconds=1),
    )
    assert result["dover_status"] == "timeout"


def test_dover_nonzero_runner_with_json_status(tmp_path, monkeypatch):
    repo = tmp_path / "DOVER"
    repo.mkdir()

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, stdout='{"dover_status":"unavailable","dover_error":"missing deps"}\n', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = evaluate_dover_quality(
        tmp_path / "highlight.mp4",
        DoverConfig(enabled=True, repo_dir=repo, python="python"),
    )
    assert result["dover_status"] == "unavailable"
    assert "missing deps" in result["dover_error"]


def test_dover_invalid_json(tmp_path, monkeypatch):
    repo = tmp_path / "DOVER"
    repo.mkdir()

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="not json\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = evaluate_dover_quality(
        tmp_path / "highlight.mp4",
        DoverConfig(enabled=True, repo_dir=repo, python="python"),
    )
    assert result["dover_status"] == "invalid_json"


def test_dover_success_normalizes_scores(tmp_path, monkeypatch):
    repo = tmp_path / "DOVER"
    repo.mkdir()

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout=(
                '{"dover_status":"success","dover_model":"dover-mobile",'
                '"dover_device":"cpu","dover_runtime_seconds":4.21,'
                '"dover_fused_overall_score":0.793,'
                '"dover_raw_technical_score":0.124,'
                '"dover_raw_visual_aesthetic_score":-0.031,'
                '"dover_reference_percentiles":null}\n'
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = evaluate_dover_quality(
        tmp_path / "highlight.mp4",
        DoverConfig(enabled=True, repo_dir=repo, python="python"),
    )
    assert result["dover_status"] == "success"
    assert result["dover_fused_overall_score"] == 0.793
    assert result["dover_raw_visual_aesthetic_score"] == -0.031
