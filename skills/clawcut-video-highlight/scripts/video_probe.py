from __future__ import annotations

import argparse
import json
import logging
from fractions import Fraction
from pathlib import Path
from typing import Any

from utils import SkillError, load_config, require_file, run_command


def _parse_fraction(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None


def probe_video(
    input_video: Path,
    ffprobe_command: str = "ffprobe",
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    require_file(input_video, "输入视频")
    args = [
        ffprobe_command,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,width,height,r_frame_rate,avg_frame_rate",
        "-of",
        "json",
        str(input_video),
    ]
    completed = run_command(args, logger=logger)
    raw = json.loads(completed.stdout)
    streams = raw.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video_stream:
        raise SkillError(f"未找到视频流：{input_video}")

    duration = float(raw.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise SkillError(f"无法读取有效的视频时长：{input_video}")

    fps = _parse_fraction(video_stream.get("avg_frame_rate")) or _parse_fraction(
        video_stream.get("r_frame_rate")
    )
    return {
        "path": str(input_video),
        "duration": duration,
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "fps": fps,
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="使用 ffprobe 探测视频基础信息。")
    parser.add_argument("input_video", type=Path)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    info = probe_video(args.input_video, config.get("ffmpeg", {}).get("probe_command", "ffprobe"))
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
