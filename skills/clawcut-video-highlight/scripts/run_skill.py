from __future__ import annotations

import argparse
import traceback
from pathlib import Path
from typing import Any

from ffmpeg_editor import render_highlight
from make_preview import make_preview
from mock_llm import generate_mock_plan
from plan_validator import assert_valid_plan
from utils import SkillError, ensure_dir, load_config, setup_logger, write_json, write_text
from video_probe import probe_video


def _output_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "videos": output_dir / "videos",
        "reports": output_dir / "reports",
        "logs": output_dir / "logs",
        "work": output_dir / "work",
        "highlight": output_dir / "videos" / "highlight.mp4",
        "preview": output_dir / "videos" / "preview.mp4",
        "segments": output_dir / "reports" / "segments.json",
        "report": output_dir / "reports" / "report.md",
        "log": output_dir / "logs" / "run.log",
    }


def _write_report(
    report_path: Path,
    input_video: Path,
    instruction: str,
    video_info: dict[str, Any],
    plan: dict[str, Any],
    validation: dict[str, Any],
    highlight_path: Path,
    preview_path: Path,
) -> None:
    lines = [
        "# ClawCut 视频高光剪辑报告",
        "",
        "## 输入信息",
        "",
        f"- 原始视频：`{input_video}`",
        f"- 用户指令：{instruction}",
        f"- 目标时长：{validation['target_duration']:.3f} 秒",
        f"- 实际可实现目标时长：{validation['effective_target_duration']:.3f} 秒",
        "",
        "## 视频探测结果",
        "",
        f"- 视频总时长：{video_info['duration']:.3f} 秒",
        f"- 分辨率：{video_info['width']}x{video_info['height']}",
        f"- 帧率：{video_info.get('fps')}",
        f"- 是否包含音频：{video_info['has_audio']}",
        "",
        "## 剪辑方案",
        "",
        f"- 视频类型：{plan['video_type']}",
        f"- 最终片段数：{len(plan['final_segments'])}",
        f"- 最终总时长：{validation['total_duration']:.3f} 秒",
        f"- 与目标时长差值：{validation['duration_delta']:.3f} 秒",
        "",
        "## 高光片段",
        "",
    ]
    for index, segment in enumerate(plan["final_segments"], start=1):
        lines.append(
            f"{index}. {float(segment['start']):.3f}s - {float(segment['end']):.3f}s: {segment['reason']}"
        )
    lines.extend(
        [
            "",
            "## 输出文件",
            "",
            f"- 高光视频：`{highlight_path}`",
            f"- 预览视频：`{preview_path}`",
            f"- 方案校验通过：{validation['ok']}",
        ]
    )
    if validation.get("warnings"):
        lines.extend(["", "## 警告", ""])
        lines.extend(f"- {warning}" for warning in validation["warnings"])
    write_text(report_path, "\n".join(lines) + "\n")


def run_skill(input_video: Path, instruction: str, target_duration: float, output_dir: Path) -> dict[str, Any]:
    paths = _output_paths(output_dir)
    for key in ("videos", "reports", "logs", "work"):
        ensure_dir(paths[key])

    logger = setup_logger(paths["log"])
    logger.info("开始运行 ClawCut 视频高光剪辑 Skill")
    logger.info("输入视频：%s", input_video)
    logger.info("目标时长：%.3f 秒", target_duration)

    config = load_config()
    video_info = probe_video(input_video, config.get("ffmpeg", {}).get("probe_command", "ffprobe"), logger)
    logger.info("视频探测完成：%s", video_info)

    make_preview(input_video, paths["preview"], config, logger)
    logger.info("预览视频已写入：%s", paths["preview"])

    plan = generate_mock_plan(video_info, instruction, target_duration, config)
    validation = assert_valid_plan(plan, video_info["duration"], target_duration, config)
    logger.info("剪辑方案校验完成：%s", validation)

    write_json(paths["segments"], plan)
    logger.info("结构化片段方案已写入：%s", paths["segments"])

    render_highlight(
        input_video=input_video,
        final_segments=plan["final_segments"],
        output_video=paths["highlight"],
        work_dir=paths["work"],
        config=config,
        logger=logger,
    )
    logger.info("高光视频已写入：%s", paths["highlight"])

    _write_report(
        report_path=paths["report"],
        input_video=input_video,
        instruction=instruction,
        video_info=video_info,
        plan=plan,
        validation=validation,
        highlight_path=paths["highlight"],
        preview_path=paths["preview"],
    )
    logger.info("中文报告已写入：%s", paths["report"])
    logger.info("Skill 运行完成")

    return {
        "highlight": str(paths["highlight"]),
        "segments": str(paths["segments"]),
        "report": str(paths["report"]),
        "log": str(paths["log"]),
        "validation": validation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 ClawCut 视频高光剪辑 Skill。")
    parser.add_argument("--input_video", type=Path, required=True)
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--target_duration", type=float, required=True)
    parser.add_argument("--output_dir", type=Path, required=True)
    args = parser.parse_args()

    output_dir = args.output_dir
    paths = _output_paths(output_dir)
    ensure_dir(paths["logs"])
    logger = setup_logger(paths["log"])

    try:
        result = run_skill(args.input_video, args.instruction, args.target_duration, output_dir)
    except SkillError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:
        logger.error("发生未预期异常：%s", exc)
        logger.error(traceback.format_exc())
        return 1

    print("ClawCut 视频高光剪辑 Skill 运行完成。")
    print(f"高光视频：{result['highlight']}")
    print(f"片段方案：{result['segments']}")
    print(f"中文报告：{result['report']}")
    print(f"运行日志：{result['log']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
