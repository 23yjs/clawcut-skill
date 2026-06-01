from __future__ import annotations

import subprocess
from pathlib import Path

from evaluation import technical_quality as tq


def _completed(returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(["cmd"], returncode, stdout="", stderr=stderr)


def test_duplicate_source_ratio_uses_interval_union(tmp_path, monkeypatch):
    highlight = tmp_path / "highlight.mp4"
    source = tmp_path / "source.mp4"
    highlight.write_bytes(b"fake")
    source.write_bytes(b"fake")

    def fake_probe(path: Path, ffprobe_command: str = "ffprobe"):
        return {"duration": 17.0 if path == highlight else 100.0, "video_stream_present": True, "has_audio": True}

    monkeypatch.setattr(tq, "_probe_media", fake_probe)
    monkeypatch.setattr(tq, "_run", lambda command: _completed())
    result = tq.check_technical_quality(
        input_video=source,
        highlight_video=highlight,
        final_segments=[{"start": 0, "end": 10}, {"start": 8, "end": 15}],
        source_video_duration=100,
    )
    assert result["planned_total_duration"] == 17.0
    assert result["selected_source_union_duration"] == 15.0
    assert result["duplicate_source_duration"] == 2.0
    assert result["duplicate_source_ratio"] == 0.118


def test_missing_highlight_fails(tmp_path):
    result = tq.check_technical_quality(
        input_video=tmp_path / "source.mp4",
        highlight_video=tmp_path / "missing.mp4",
        final_segments=[{"start": 0, "end": 5}],
        source_video_duration=20,
    )
    assert result["technical_quality_passed"] is False
    assert result["highlight_has_audio"] is False


def test_ffprobe_failure_is_structured(tmp_path, monkeypatch):
    highlight = tmp_path / "highlight.mp4"
    highlight.write_bytes(b"fake")

    def fail_probe(path: Path, ffprobe_command: str = "ffprobe"):
        raise RuntimeError("bad media")

    monkeypatch.setattr(tq, "_probe_media", fail_probe)
    result = tq.check_technical_quality(
        input_video=tmp_path / "source.mp4",
        highlight_video=highlight,
        final_segments=[{"start": 0, "end": 5}],
        source_video_duration=20,
    )
    assert result["technical_quality_passed"] is False
    assert any("ffprobe" in error for error in result["technical_quality_errors"])


def test_black_frame_thresholds(tmp_path, monkeypatch):
    highlight = tmp_path / "highlight.mp4"
    highlight.write_bytes(b"fake")

    monkeypatch.setattr(tq, "_probe_media", lambda path, ffprobe_command="ffprobe": {"duration": 10.0, "video_stream_present": True, "has_audio": False})

    def fake_run(command):
        if any(str(part).startswith("blackdetect=") for part in command):
            return _completed(stderr="[blackdetect] black_start:0 black_end:8 black_duration:8")
        return _completed()

    monkeypatch.setattr(tq, "_run", fake_run)
    result = tq.check_technical_quality(
        input_video=tmp_path / "source.mp4",
        highlight_video=highlight,
        final_segments=[{"start": 0, "end": 10}],
        source_video_duration=100,
    )
    assert result["black_frame_ratio"] == 0.8
    assert result["technical_quality_passed"] is False
