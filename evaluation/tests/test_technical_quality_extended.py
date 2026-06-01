from __future__ import annotations

import subprocess
from pathlib import Path

from evaluation import technical_quality as tq


def test_parse_blackdetect_multiple_intervals():
    text = """
    [blackdetect] black_start:1 black_end:2.5 black_duration:1.5
    [blackdetect] black_start:5 black_end:7 black_duration:2
    """
    intervals = tq.parse_blackdetect_intervals(text)
    assert intervals == [
        {"start": 1.0, "end": 2.5, "duration": 1.5},
        {"start": 5.0, "end": 7.0, "duration": 2.0},
    ]


def test_parse_freezedetect_ignores_missing_end():
    text = """
    [freezedetect] lavfi.freezedetect.freeze_start: 1.2
    [freezedetect] lavfi.freezedetect.freeze_duration: 3.6
    [freezedetect] lavfi.freezedetect.freeze_end: 4.8
    [freezedetect] lavfi.freezedetect.freeze_start: 9.0
    """
    assert tq.parse_freezedetect_intervals(text) == [{"start": 1.2, "end": 4.8, "duration": 3.6}]


def test_parse_silencedetect_multiple_intervals():
    text = """
    [silencedetect] silence_start: 8
    [silencedetect] silence_end: 13.2 | silence_duration: 5.2
    [silencedetect] silence_start: 20
    [silencedetect] silence_end: 25 | silence_duration: 5
    """
    assert tq.parse_silencedetect_intervals(text) == [
        {"start": 8.0, "end": 13.2, "duration": 5.2},
        {"start": 20.0, "end": 25.0, "duration": 5.0},
    ]


def test_empty_filter_output_returns_empty_lists():
    assert tq.parse_blackdetect_intervals("") == []
    assert tq.parse_freezedetect_intervals("") == []
    assert tq.parse_silencedetect_intervals("") == []


def test_freeze_and_silence_warnings_are_structured(tmp_path, monkeypatch):
    highlight = tmp_path / "highlight.mp4"
    source = tmp_path / "source.mp4"
    highlight.write_bytes(b"fake")
    source.write_bytes(b"fake")

    monkeypatch.setattr(
        tq,
        "_probe_media",
        lambda path, ffprobe_command="ffprobe": {"duration": 20.0, "video_stream_present": True, "has_audio": True},
    )

    def fake_run(command):
        joined = " ".join(command)
        if "freezedetect" in joined:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="freeze_start: 1\nfreeze_duration: 4\nfreeze_end: 5")
        if "silencedetect" in joined:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="silence_start: 8\nsilence_end: 14 | silence_duration: 6")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(tq, "_run", fake_run)
    result = tq.check_technical_quality(
        input_video=source,
        highlight_video=highlight,
        final_segments=[{"start": 0, "end": 20}],
        source_video_duration=100,
    )
    assert result["freeze_frame_ratio"] == 0.2
    assert result["silence_ratio"] == 0.3
    assert result["technical_checks"]["freeze_frames"]["freeze_frame_duration"] == 4.0
    assert any("freeze_frame_ratio" in warning for warning in result["technical_quality_warnings"])
    assert any("持续静音" in warning for warning in result["technical_quality_warnings"])
