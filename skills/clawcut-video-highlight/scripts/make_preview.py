from __future__ import annotations

import argparse
import logging
from pathlib import Path

from utils import ensure_dir, load_config, require_file, run_command


def make_preview(
    input_video: Path,
    output_video: Path,
    config: dict,
    logger: logging.Logger | None = None,
) -> Path:
    require_file(input_video, "输入视频")
    ensure_dir(output_video.parent)

    ffmpeg_config = config.get("ffmpeg", {})
    preview_config = config.get("preview", {})
    ffmpeg_command = ffmpeg_config.get("command", "ffmpeg")
    overwrite_flag = "-y" if ffmpeg_config.get("overwrite", True) else "-n"
    width = int(preview_config.get("width", 640))

    args = [
        ffmpeg_command,
        overwrite_flag,
        "-i",
        str(input_video),
        "-vf",
        f"scale={width}:-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        str(preview_config.get("video_bitrate", "700k")),
        "-c:a",
        "aac",
        "-b:a",
        str(preview_config.get("audio_bitrate", "64k")),
        "-movflags",
        "+faststart",
        str(output_video),
    ]
    run_command(args, logger=logger)
    return output_video


def main() -> int:
    parser = argparse.ArgumentParser(description="生成低码率连续预览视频。")
    parser.add_argument("input_video", type=Path)
    parser.add_argument("output_video", type=Path)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    make_preview(args.input_video, args.output_video, config)
    print(str(args.output_video))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
