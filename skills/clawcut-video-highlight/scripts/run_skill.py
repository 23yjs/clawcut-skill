from __future__ import annotations

import argparse
import re
import traceback
from pathlib import Path
from typing import Any

from ffmpeg_editor import render_highlight
from llm_client import create_llm_client
from make_preview import make_preview
from plan_validator import assert_valid_plan
from utils import SkillError, ensure_dir, load_config, setup_logger, write_json, write_text
from video_probe import probe_video


def _mask_url(url: str) -> str:
    return url.split("?", 1)[0] + "?..." if "?" in url else url


def _safe_output_name(input_video: Path) -> str:
    name = re.sub(r"[^\w.-]+", "_", input_video.stem, flags=re.UNICODE).strip("._")
    return name or "video"


def _output_paths(output_dir: Path, input_video: Path) -> dict[str, Path]:
    run_dir = output_dir / _safe_output_name(input_video)
    return {
        "root": output_dir,
        "run": run_dir,
        "videos": run_dir / "videos",
        "reports": run_dir / "reports",
        "logs": run_dir / "logs",
        "work": run_dir / "work",
        "highlight": run_dir / "videos" / "highlight.mp4",
        "preview": run_dir / "videos" / "preview.mp4",
        "segments": run_dir / "reports" / "segments.json",
        "report": run_dir / "reports" / "report.md",
        "log": run_dir / "logs" / "run.log",
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
        f"- 输出目录：`{highlight_path.parents[1]}`",
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
        title = segment.get("title", f"高光片段 {index}")
        role = segment.get("role", "未标注作用")
        reason = segment.get("reason", "")
        lines.append(
            f"{index}. {float(segment['start']):.3f}s - {float(segment['end']):.3f}s "
            f"[{role}] {title}：{reason}"
        )
    if plan.get("overall_rationale"):
        lines.extend(["", "## 整体剪辑思路", "", str(plan["overall_rationale"])])
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


def run_skill(
    input_video: Path,
    instruction: str,
    target_duration: float,
    output_dir: Path,
    llm_video_url: str | None = None,
) -> dict[str, Any]:
    paths = _output_paths(output_dir, input_video)
    for key in ("videos", "reports", "logs", "work"):
        ensure_dir(paths[key])

    logger = setup_logger(paths["log"])
    logger.info("开始运行 ClawCut 视频高光剪辑 Skill")
    logger.info("输入视频：%s", input_video)
    logger.info("本次输出目录：%s", paths["run"])
    logger.info("目标时长：%.3f 秒", target_duration)

    config = load_config()
    if llm_video_url:
        config.setdefault("llm", {})
        config["llm"]["video_url"] = llm_video_url
        config["llm"]["video_input_mode"] = "url"
        logger.info("LLM 将使用外部视频 URL 输入：%s", _mask_url(llm_video_url))
    video_info = probe_video(input_video, config.get("ffmpeg", {}).get("probe_command", "ffprobe"), logger)
    logger.info("视频探测完成：%s", video_info)

    make_preview(input_video, paths["preview"], config, logger)
    logger.info("预览视频已写入：%s", paths["preview"])

    client = create_llm_client(config, logger=logger)
    plan = client.generate_edit_plan(str(paths["preview"]), instruction, target_duration, video_info, config)
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
        "output_dir": str(paths["run"]),
        "validation": validation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 ClawCut 视频高光剪辑 Skill。")
    parser.add_argument("--input_video", type=Path, required=True)
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--target_duration", type=float, required=True)
    parser.add_argument("--output_dir", type=Path, required=True)
    parser.add_argument(
        "--llm_video_url",
        default=None,
        help="可选：提供给大模型的视频 URL，例如 TOS 公开或签名 URL。未提供时使用本地 preview 的 data URL。",
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    paths = _output_paths(output_dir, args.input_video)
    ensure_dir(paths["logs"])
    logger = setup_logger(paths["log"])

    try:
        result = run_skill(
            args.input_video,
            args.instruction,
            args.target_duration,
            output_dir,
            llm_video_url=args.llm_video_url,
        )
    except SkillError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:
        logger.error("发生未预期异常：%s", exc)
        logger.error(traceback.format_exc())
        return 1

    print("ClawCut 视频高光剪辑 Skill 运行完成。")
    print(f"输出目录：{result['output_dir']}")
    print(f"高光视频：{result['highlight']}")
    print(f"片段方案：{result['segments']}")
    print(f"中文报告：{result['report']}")
    print(f"运行日志：{result['log']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
