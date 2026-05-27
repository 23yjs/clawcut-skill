from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from utils import SkillError, ensure_dir, load_config, read_json, require_file, run_command, seconds


def _write_concat_list(path: Path, segment_paths: list[Path]) -> None:
    lines = []
    for segment_path in segment_paths:
        safe_path = str(segment_path.resolve()).replace("'", "'\\''")
        lines.append(f"file '{safe_path}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_highlight(
    input_video: Path,
    final_segments: list[dict[str, Any]],
    output_video: Path,
    work_dir: Path,
    config: dict,
    logger: logging.Logger | None = None,
) -> Path:
    require_file(input_video, "输入视频")
    if not final_segments:
        raise SkillError("缺少 final_segments，无法渲染高光视频")

    ensure_dir(output_video.parent)
    ensure_dir(work_dir)

    ffmpeg_config = config.get("ffmpeg", {})
    render_config = config.get("render", {})
    ffmpeg_command = ffmpeg_config.get("command", "ffmpeg")
    overwrite_flag = "-y" if ffmpeg_config.get("overwrite", True) else "-n"

    temp_segments: list[Path] = []
    for index, segment in enumerate(final_segments):
        start = float(segment["start"])
        end = float(segment["end"])
        duration = end - start
        temp_path = work_dir / f"segment_{index:03d}.mp4"
        args = [
            ffmpeg_command,
            overwrite_flag,
            "-ss",
            seconds(start),
            "-i",
            str(input_video),
            "-t",
            seconds(duration),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            str(render_config.get("video_codec", "libx264")),
            "-preset",
            str(render_config.get("preset", "veryfast")),
            "-crf",
            str(render_config.get("crf", 23)),
            "-c:a",
            str(render_config.get("audio_codec", "aac")),
            "-movflags",
            "+faststart",
            str(temp_path),
        ]
        run_command(args, logger=logger)
        temp_segments.append(temp_path)

    concat_list = work_dir / "concat.txt"
    _write_concat_list(concat_list, temp_segments)
    concat_args = [
        ffmpeg_command,
        overwrite_flag,
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_video),
    ]
    run_command(concat_args, logger=logger)
    return output_video


def main() -> int:
    parser = argparse.ArgumentParser(description="使用 ffmpeg 渲染最终高光片段。")
    parser.add_argument("--input_video", type=Path, required=True)
    parser.add_argument("--plan_json", type=Path, required=True)
    parser.add_argument("--output_video", type=Path, required=True)
    parser.add_argument("--work_dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    plan = read_json(args.plan_json)
    render_highlight(args.input_video, plan["final_segments"], args.output_video, args.work_dir, config)
    print(str(args.output_video))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
