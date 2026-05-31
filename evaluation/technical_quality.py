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


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


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


def _blackdetect_duration(stderr_text: str) -> float:
    total = 0.0
    for match in re.finditer(r"black_duration:([0-9.]+)", stderr_text):
        total += float(match.group(1))
    return total


def check_technical_quality(
    *,
    input_video: Path,
    highlight_video: Path,
    final_segments: list[dict[str, Any]],
    source_video_duration: float | None,
    ffprobe_command: str = "ffprobe",
    ffmpeg_command: str = "ffmpeg",
) -> dict[str, Any]:
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
    black_frame_duration = 0.0

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
        blackdetect = _run(
            [
                ffmpeg_command,
                "-v",
                "warning",
                "-i",
                str(highlight_video),
                "-vf",
                "blackdetect=d=0.5:pix_th=0.10",
                "-an",
                "-f",
                "null",
                "-",
            ]
        )
        black_frame_duration = _blackdetect_duration((blackdetect.stderr or "") + "\n" + (blackdetect.stdout or ""))

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
    if rendered_duration_error_ratio is not None and rendered_duration_error_ratio > 0.20:
        errors.append(f"rendered_duration_error_ratio 超过 0.20：{rendered_duration_error_ratio:.3f}")
    if black_frame_ratio >= 0.80:
        errors.append(f"black_frame_ratio >= 0.80：{black_frame_ratio:.3f}")
    elif black_frame_ratio >= 0.20:
        warnings.append(f"black_frame_ratio >= 0.20：{black_frame_ratio:.3f}")
    if duplicate_source_ratio > 0.10:
        warnings.append(f"duplicate_source_ratio 较高：{duplicate_source_ratio:.3f}")

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
        "black_frame_duration": round(black_frame_duration, 3),
        "black_frame_ratio": round(black_frame_ratio, 3),
        "compression_ratio": round(compression_ratio, 3) if compression_ratio is not None else None,
        "selected_source_union_duration": round(selected_source_union_duration, 3),
        "duplicate_source_duration": round(duplicate_source_duration, 3),
        "duplicate_source_ratio": round(duplicate_source_ratio, 3),
    }
