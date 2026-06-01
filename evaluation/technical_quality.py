from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

try:
    from .interval_utils import intervals_duration, normalize_intervals
except ImportError:  # pragma: no cover - script mode
    from interval_utils import intervals_duration, normalize_intervals


DEFAULT_TECHNICAL_QUALITY_CONFIG: dict[str, Any] = {
    "blackdetect": {
        "min_duration_seconds": 0.5,
        "pixel_threshold": 0.10,
        "warning_ratio": 0.20,
        "error_ratio": 0.80,
    },
    "freezedetect": {
        "noise_db": -60,
        "min_duration_seconds": 2.0,
        "warning_ratio": 0.15,
        "error_ratio": 0.50,
    },
    "silencedetect": {
        "noise_db": -50,
        "min_duration_seconds": 5.0,
        "warning_ratio": 0.50,
    },
    "rendered_duration": {
        "max_error_ratio": 0.20,
    },
    "duplicate_source": {
        "warning_ratio": 0.10,
    },
}


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_technical_quality_config(config_path: Path | None = None) -> dict[str, Any]:
    default_path = Path(__file__).resolve().parent / "config" / "default.yaml"
    path = config_path or default_path
    if not path.exists():
        return dict(DEFAULT_TECHNICAL_QUALITY_CONFIG)
    try:
        import yaml
    except ImportError:
        return dict(DEFAULT_TECHNICAL_QUALITY_CONFIG)
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    technical = payload.get("technical_quality", payload)
    if not isinstance(technical, dict):
        return dict(DEFAULT_TECHNICAL_QUALITY_CONFIG)
    return _deep_merge(DEFAULT_TECHNICAL_QUALITY_CONFIG, technical)


def _probe_media(path: Path, ffprobe_command: str = "ffprobe") -> dict[str, Any]:
    command = [
        ffprobe_command,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,width,height",
        "-of",
        "json",
        str(path),
    ]
    process = _run(command)
    if process.returncode != 0:
        raise RuntimeError(process.stderr.strip() or process.stdout.strip() or "ffprobe failed")
    payload = json.loads(process.stdout)
    streams = payload.get("streams", [])
    return {
        "duration": float(payload.get("format", {}).get("duration", 0) or 0),
        "video_stream_present": any(stream.get("codec_type") == "video" for stream in streams),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
    }


def _sum_segment_duration(final_segments: list[dict[str, Any]]) -> float:
    return round(sum(max(0.0, float(segment["end"]) - float(segment["start"])) for segment in final_segments), 6)


def parse_blackdetect_intervals(stderr_text: str) -> list[dict[str, float]]:
    intervals: list[dict[str, float]] = []
    pattern = re.compile(
        r"black_start:(?P<start>[0-9.]+)\s+black_end:(?P<end>[0-9.]+)\s+black_duration:(?P<duration>[0-9.]+)"
    )
    for match in pattern.finditer(stderr_text):
        intervals.append(
            {
                "start": float(match.group("start")),
                "end": float(match.group("end")),
                "duration": float(match.group("duration")),
            }
        )
    return intervals


def parse_freezedetect_intervals(stderr_text: str) -> list[dict[str, float]]:
    intervals: list[dict[str, float]] = []
    current_start: float | None = None
    current_duration: float | None = None
    for line in stderr_text.splitlines():
        start_match = re.search(r"freeze_start:\s*([0-9.]+)", line)
        if start_match:
            current_start = float(start_match.group(1))
            current_duration = None
        duration_match = re.search(r"freeze_duration:\s*([0-9.]+)", line)
        if duration_match:
            current_duration = float(duration_match.group(1))
        end_match = re.search(r"freeze_end:\s*([0-9.]+)", line)
        if end_match and current_start is not None:
            end = float(end_match.group(1))
            duration = current_duration if current_duration is not None else max(0.0, end - current_start)
            if end > current_start:
                intervals.append({"start": current_start, "end": end, "duration": duration})
            current_start = None
            current_duration = None
    return intervals


def parse_silencedetect_intervals(stderr_text: str) -> list[dict[str, float]]:
    intervals: list[dict[str, float]] = []
    current_start: float | None = None
    for line in stderr_text.splitlines():
        start_match = re.search(r"silence_start:\s*([0-9.]+)", line)
        if start_match:
            current_start = float(start_match.group(1))
        end_match = re.search(r"silence_end:\s*([0-9.]+).*?silence_duration:\s*([0-9.]+)", line)
        if end_match and current_start is not None:
            end = float(end_match.group(1))
            duration = float(end_match.group(2))
            if end > current_start:
                intervals.append({"start": current_start, "end": end, "duration": duration})
            current_start = None
    return intervals


def _duration(intervals: list[dict[str, float]]) -> float:
    return round(sum(max(0.0, item["duration"]) for item in intervals), 6)


def _blackdetect_duration(stderr_text: str) -> float:
    """Backward-compatible helper for older tests and callers."""
    return _duration(parse_blackdetect_intervals(stderr_text))


def check_technical_quality(
    *,
    input_video: Path,
    highlight_video: Path,
    final_segments: list[dict[str, Any]],
    source_video_duration: float | None,
    ffprobe_command: str = "ffprobe",
    ffmpeg_command: str = "ffmpeg",
    config_path: Path | None = None,
) -> dict[str, Any]:
    config = load_technical_quality_config(config_path)
    errors: list[str] = []
    warnings: list[str] = []
    planned_total_duration = _sum_segment_duration(final_segments)
    selected_source_union_duration = intervals_duration(
        normalize_intervals({"start": segment["start"], "end": segment["end"]} for segment in final_segments)
    )
    duplicate_source_duration = max(0.0, planned_total_duration - selected_source_union_duration)
    duplicate_source_ratio = duplicate_source_duration / planned_total_duration if planned_total_duration > 0 else 0.0

    rendered_duration = 0.0
    video_stream_present = False
    highlight_has_audio = False
    source_has_audio: bool | None = None
    decode_success = False
    decode_error = ""
    black_intervals: list[dict[str, float]] = []
    black_frame_duration = 0.0
    freeze_intervals: list[dict[str, float]] = []
    freeze_frame_duration = 0.0
    silence_intervals: list[dict[str, float]] = []
    silence_duration = 0.0

    if not highlight_video.exists():
        errors.append(f"highlight.mp4 不存在：{highlight_video}")
    else:
        try:
            highlight_probe = _probe_media(highlight_video, ffprobe_command)
            rendered_duration = float(highlight_probe["duration"])
            video_stream_present = bool(highlight_probe["video_stream_present"])
            highlight_has_audio = bool(highlight_probe["has_audio"])
        except Exception as exc:
            errors.append(f"highlight.mp4 ffprobe 失败：{exc}")

    if input_video.exists():
        try:
            source_probe = _probe_media(input_video, ffprobe_command)
            source_has_audio = bool(source_probe["has_audio"])
            if source_video_duration is None:
                source_video_duration = float(source_probe["duration"])
        except Exception as exc:
            warnings.append(f"原视频 ffprobe 失败，无法确认音频一致性：{exc}")
    else:
        warnings.append(f"原视频不存在，无法确认音频一致性：{input_video}")

    if highlight_video.exists():
        decode = _run([ffmpeg_command, "-v", "error", "-i", str(highlight_video), "-f", "null", "-"])
        decode_success = decode.returncode == 0
        decode_error = (decode.stderr or decode.stdout or "").strip()
        black_cfg = config["blackdetect"]
        blackdetect = _run(
            [
                ffmpeg_command,
                "-v",
                "warning",
                "-i",
                str(highlight_video),
                "-vf",
                f"blackdetect=d={float(black_cfg['min_duration_seconds'])}:pix_th={float(black_cfg['pixel_threshold'])}",
                "-an",
                "-f",
                "null",
                "-",
            ]
        )
        black_intervals = parse_blackdetect_intervals((blackdetect.stderr or "") + "\n" + (blackdetect.stdout or ""))
        black_frame_duration = _duration(black_intervals)

        freeze_cfg = config["freezedetect"]
        freezedetect = _run(
            [
                ffmpeg_command,
                "-v",
                "warning",
                "-i",
                str(highlight_video),
                "-vf",
                f"freezedetect=n={float(freeze_cfg['noise_db'])}dB:d={float(freeze_cfg['min_duration_seconds'])}",
                "-an",
                "-f",
                "null",
                "-",
            ]
        )
        freeze_intervals = parse_freezedetect_intervals((freezedetect.stderr or "") + "\n" + (freezedetect.stdout or ""))
        freeze_frame_duration = _duration(freeze_intervals)

        if highlight_has_audio:
            silence_cfg = config["silencedetect"]
            silencedetect = _run(
                [
                    ffmpeg_command,
                    "-v",
                    "warning",
                    "-i",
                    str(highlight_video),
                    "-af",
                    f"silencedetect=noise={float(silence_cfg['noise_db'])}dB:d={float(silence_cfg['min_duration_seconds'])}",
                    "-f",
                    "null",
                    "-",
                ]
            )
            silence_intervals = parse_silencedetect_intervals((silencedetect.stderr or "") + "\n" + (silencedetect.stdout or ""))
            silence_duration = _duration(silence_intervals)

    rendered_duration_delta = abs(rendered_duration - planned_total_duration) if planned_total_duration > 0 else None
    rendered_duration_error_ratio = (
        rendered_duration_delta / planned_total_duration if rendered_duration_delta is not None and planned_total_duration > 0 else None
    )
    black_frame_ratio = black_frame_duration / rendered_duration if rendered_duration > 0 else 0.0
    compression_ratio = (
        rendered_duration / float(source_video_duration)
        if source_video_duration is not None and float(source_video_duration) > 0
        else None
    )

    if highlight_video.exists() and not video_stream_present:
        errors.append("highlight.mp4 没有视频流")
    if highlight_video.exists() and rendered_duration <= 0:
        errors.append("highlight.mp4 rendered_duration <= 0")
    if highlight_video.exists() and not decode_success:
        errors.append("highlight.mp4 解码失败")
    if source_has_audio is True and not highlight_has_audio:
        errors.append("原视频有音频但成片无音频")
    if rendered_duration_error_ratio is not None and rendered_duration_error_ratio > float(config["rendered_duration"]["max_error_ratio"]):
        errors.append(f"rendered_duration_error_ratio 超过 0.20：{rendered_duration_error_ratio:.3f}")
    if black_frame_ratio >= float(config["blackdetect"]["error_ratio"]):
        errors.append(f"black_frame_ratio >= 0.80：{black_frame_ratio:.3f}")
    elif black_frame_ratio >= float(config["blackdetect"]["warning_ratio"]):
        warnings.append(f"black_frame_ratio >= 0.20：{black_frame_ratio:.3f}")
    freeze_frame_ratio = freeze_frame_duration / rendered_duration if rendered_duration > 0 else 0.0
    if freeze_frame_ratio >= float(config["freezedetect"]["error_ratio"]):
        errors.append(f"freeze_frame_ratio >= {float(config['freezedetect']['error_ratio']):.2f}：{freeze_frame_ratio:.3f}")
    elif freeze_frame_ratio >= float(config["freezedetect"]["warning_ratio"]):
        warnings.append(f"freeze_frame_ratio >= {float(config['freezedetect']['warning_ratio']):.2f}：{freeze_frame_ratio:.3f}")

    silence_ratio = silence_duration / rendered_duration if rendered_duration > 0 else 0.0
    if source_has_audio is True and highlight_has_audio:
        max_silence = max((item["duration"] for item in silence_intervals), default=0.0)
        if max_silence >= float(config["silencedetect"]["min_duration_seconds"]):
            warnings.append(f"存在持续静音片段：最长 {max_silence:.3f} 秒")
        if silence_ratio >= float(config["silencedetect"]["warning_ratio"]):
            warnings.append(f"silence_ratio >= {float(config['silencedetect']['warning_ratio']):.2f}：{silence_ratio:.3f}")

    if duplicate_source_ratio > float(config["duplicate_source"]["warning_ratio"]):
        warnings.append(f"duplicate_source_ratio 较高：{duplicate_source_ratio:.3f}")

    technical_checks = {
        "decode": {
            "decode_success": decode_success,
            "decode_error": decode_error,
        },
        "audio_stream": {
            "source_has_audio": source_has_audio,
            "highlight_has_audio": highlight_has_audio,
            "audio_stream_consistent": not (source_has_audio is True and not highlight_has_audio),
        },
        "black_frames": {
            "black_intervals": black_intervals,
            "black_frame_duration": round(black_frame_duration, 3),
            "black_frame_ratio": round(black_frame_ratio, 3),
        },
        "freeze_frames": {
            "freeze_detect_enabled": highlight_video.exists(),
            "freeze_intervals": freeze_intervals,
            "freeze_frame_duration": round(freeze_frame_duration, 3),
            "freeze_frame_ratio": round(freeze_frame_ratio, 3),
        },
        "silence": {
            "silence_detect_enabled": bool(highlight_has_audio),
            "silence_intervals": silence_intervals,
            "silence_duration": round(silence_duration, 3),
            "silence_ratio": round(silence_ratio, 3),
        },
        "duration_consistency": {
            "planned_total_duration": round(planned_total_duration, 3),
            "rendered_duration": round(rendered_duration, 3),
            "rendered_duration_delta": round(rendered_duration_delta, 3) if rendered_duration_delta is not None else None,
            "rendered_duration_error_ratio": round(rendered_duration_error_ratio, 3) if rendered_duration_error_ratio is not None else None,
        },
        "duplicate_source_intervals": {
            "selected_source_union_duration": round(selected_source_union_duration, 3),
            "duplicate_source_duration": round(duplicate_source_duration, 3),
            "duplicate_source_ratio": round(duplicate_source_ratio, 3),
        },
    }

    return {
        "technical_quality_passed": not errors,
        "technical_quality_errors": errors,
        "technical_quality_warnings": warnings,
        "planned_total_duration": round(planned_total_duration, 3),
        "rendered_duration": round(rendered_duration, 3),
        "rendered_duration_delta": round(rendered_duration_delta, 3) if rendered_duration_delta is not None else None,
        "rendered_duration_error_ratio": round(rendered_duration_error_ratio, 3) if rendered_duration_error_ratio is not None else None,
        "video_stream_present": video_stream_present,
        "source_has_audio": source_has_audio,
        "highlight_has_audio": highlight_has_audio,
        "audio_stream_consistent": not (source_has_audio is True and not highlight_has_audio),
        "decode_success": decode_success,
        "decode_error": decode_error,
        "black_intervals": black_intervals,
        "black_frame_duration": round(black_frame_duration, 3),
        "black_frame_ratio": round(black_frame_ratio, 3),
        "freeze_detect_enabled": highlight_video.exists(),
        "freeze_intervals": freeze_intervals,
        "freeze_frame_duration": round(freeze_frame_duration, 3),
        "freeze_frame_ratio": round(freeze_frame_ratio, 3),
        "silence_detect_enabled": bool(highlight_has_audio),
        "silence_intervals": silence_intervals,
        "silence_duration": round(silence_duration, 3),
        "silence_ratio": round(silence_ratio, 3),
        "compression_ratio": round(compression_ratio, 3) if compression_ratio is not None else None,
        "selected_source_union_duration": round(selected_source_union_duration, 3),
        "duplicate_source_duration": round(duplicate_source_duration, 3),
        "duplicate_source_ratio": round(duplicate_source_ratio, 3),
        "technical_checks": technical_checks,
    }
