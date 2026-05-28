from __future__ import annotations

import argparse
import re
import traceback
from pathlib import Path
from typing import Any

from ffmpeg_editor import render_highlight
from llm_client import MockLLMClient, create_llm_client
from make_preview import make_preview
from plan_validator import assert_valid_plan
from utils import SkillError, ensure_dir, load_config, setup_logger, write_json, write_text
from video_probe import probe_video


def _mask_url(url: str) -> str:
    return url.split("?", 1)[0] + "?..." if "?" in url else url


def _safe_output_name(input_video: Path) -> str:
    name = re.sub(r"[^\w.-]+", "_", input_video.stem, flags=re.UNICODE).strip("._")
    return name or "video"


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def _format_list(values: Any) -> str:
    if not values:
        return "无"
    if isinstance(values, list):
        return "、".join(str(item) for item in values) or "无"
    return str(values)


def _attach_pipeline_metadata(
    plan: dict[str, Any],
    model_video_input_source: str,
    model_video_input_path_or_url: str,
    final_edit_source: Path,
    preview_path: Path | None,
) -> dict[str, Any]:
    plan["model_video_input_source"] = model_video_input_source
    plan["model_video_input_path_or_url"] = model_video_input_path_or_url
    plan["final_edit_source"] = str(final_edit_source)
    plan["preview_path"] = str(preview_path) if preview_path else ""
    return plan


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
        "result_summary": run_dir / "reports" / "result_summary.json",
        "log": run_dir / "logs" / "run.log",
    }


def _summary_segments(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not plan:
        return []
    segments = []
    for segment in plan.get("final_segments", []):
        segments.append(
            {
                "start": segment.get("start"),
                "end": segment.get("end"),
                "title": segment.get("title", ""),
                "role": segment.get("role", ""),
                "reason": segment.get("reason", ""),
            }
        )
    return segments


def _write_success_summary(
    summary_path: Path,
    paths: dict[str, Path],
    input_video: Path,
    instruction: str,
    target_duration: float,
    plan: dict[str, Any],
    validation: dict[str, Any],
    preview_path: Path | None,
) -> None:
    write_json(
        summary_path,
        {
            "status": "success",
            "input_video": str(input_video),
            "instruction": instruction,
            "target_duration": float(target_duration),
            "highlight_video": str(paths["highlight"]),
            "segments_json": str(paths["segments"]),
            "report_md": str(paths["report"]),
            "run_log": str(paths["log"]),
            "preview_video": str(preview_path) if preview_path else "",
            "model_video_input_source": plan.get("model_video_input_source", ""),
            "model_video_input_path_or_url": plan.get("model_video_input_path_or_url", ""),
            "final_edit_source": plan.get("final_edit_source", str(input_video)),
            "final_segments": _summary_segments(plan),
            "warnings": validation.get("warnings", []),
        },
    )


def _write_failure_summary(
    summary_path: Path,
    paths: dict[str, Path],
    input_video: Path,
    instruction: str,
    target_duration: float,
    error_type: str,
    error_message: str,
) -> None:
    write_json(
        summary_path,
        {
            "status": "failed",
            "input_video": str(input_video),
            "instruction": instruction,
            "target_duration": float(target_duration),
            "error_type": error_type,
            "error_message": error_message,
            "run_log": str(paths["log"]),
            "partial_outputs": {
                "preview_video": str(paths["preview"]) if paths["preview"].exists() else "",
                "segments_json": str(paths["segments"]) if paths["segments"].exists() else "",
                "report_md": str(paths["report"]) if paths["report"].exists() else "",
            },
        },
    )


def _write_report(
    report_path: Path,
    input_video: Path,
    instruction: str,
    video_info: dict[str, Any],
    plan: dict[str, Any],
    validation: dict[str, Any],
    highlight_path: Path,
    preview_path: Path | None,
    segments_path: Path,
    result_summary_path: Path,
    run_log_path: Path,
) -> None:
    highlight_definition = plan.get("highlight_definition", {})
    chunking_strategy = plan.get("chunking_strategy", {})
    self_check = plan.get("self_check", {})
    lines = [
        "# ClawCut 视频高光剪辑报告",
        "",
        "## 输入视频信息",
        "",
        f"- input_video：`{input_video}`",
        f"- 视频总时长：{video_info['duration']:.3f} 秒",
        f"- 分辨率：{video_info['width']}x{video_info['height']}",
        f"- 帧率：{video_info.get('fps')}",
        f"- 是否包含音频：{video_info['has_audio']}",
        f"- 用户指令：{instruction}",
        f"- 目标时长：{validation['target_duration']:.3f} 秒",
        "",
        "## 模型输入信息",
        "",
        f"- model_video_input_source：`{plan.get('model_video_input_source', '')}`",
        f"- model_video_input_path_or_url：`{_mask_url(str(plan.get('model_video_input_path_or_url', '')))}`",
        f"- preview_path：`{plan.get('preview_path') or '未生成'}`",
        f"- final_edit_source：`{plan.get('final_edit_source', input_video)}`",
        "- 说明：模型看到的是用户提供的视频 URL 或本地 preview；最终出片始终基于原始 input_video。",
        "",
        "## 阶段 1：视频理解与任务化高光定义",
        "",
        f"- 视频类型：{plan['video_type']}",
        f"- 类型置信度：{plan.get('type_confidence')}",
        f"- 用户意图：{plan.get('user_intent')}",
        f"- 高光目标：{highlight_definition.get('goal', '')}",
        f"- 必须包含：{_format_list(highlight_definition.get('must_include'))}",
        f"- 避免内容：{_format_list(highlight_definition.get('avoid'))}",
        f"- 选择逻辑：{highlight_definition.get('selection_logic', '')}",
        f"- 定义来源：{highlight_definition.get('definition_source', '')}",
        "",
        "## 阶段 2：语义分块与片段评分",
        "",
        f"- 分块方法：{chunking_strategy.get('method', '')}",
        f"- 分块原因：{chunking_strategy.get('reason', '')}",
        "",
        "### Chunks",
        "",
        "| id | start | end | title | semantic_role | expected_highlight_value | summary |",
        "| --- | ---: | ---: | --- | --- | --- | --- |",
    ]
    for chunk in plan.get("chunks", []):
        lines.append(
            "| {id} | {start:.3f} | {end:.3f} | {title} | {role} | {value} | {summary} |".format(
                id=_md(chunk.get("id")),
                start=float(chunk.get("start", 0)),
                end=float(chunk.get("end", 0)),
                title=_md(chunk.get("title")),
                role=_md(chunk.get("semantic_role")),
                value=_md(chunk.get("expected_highlight_value")),
                summary=_md(chunk.get("summary")),
            )
        )
    lines.extend(
        [
            "",
            "### Chunk Reviews",
            "",
            "| chunk_id | overall_score | should_select | refined_start | refined_end | reason |",
            "| --- | ---: | --- | ---: | ---: | --- |",
        ]
    )
    for review in plan.get("chunk_reviews", []):
        lines.append(
            "| {chunk_id} | {score} | {select} | {start:.3f} | {end:.3f} | {reason} |".format(
                chunk_id=_md(review.get("chunk_id")),
                score=review.get("overall_score"),
                select=review.get("should_select"),
                start=float(review.get("refined_start", 0)),
                end=float(review.get("refined_end", 0)),
                reason=_md(review.get("reason")),
            )
        )
    lines.extend(
        [
            "",
            "## 阶段 3：全局剪辑规划与自检",
            "",
            f"- 最终片段数：{len(plan['final_segments'])}",
            f"- 最终总时长：{validation['total_duration']:.3f} 秒",
            f"- 与目标时长差值：{validation['duration_delta']:.3f} 秒",
            "",
            "| start | end | duration | title | role | source_chunk_id | reason |",
            "| ---: | ---: | ---: | --- | --- | --- | --- |",
        ]
    )
    for segment in plan["final_segments"]:
        start = float(segment["start"])
        end = float(segment["end"])
        lines.append(
            "| {start:.3f} | {end:.3f} | {duration:.3f} | {title} | {role} | {source} | {reason} |".format(
                start=start,
                end=end,
                duration=end - start,
                title=_md(segment.get("title")),
                role=_md(segment.get("role")),
                source=_md(segment.get("source_chunk_id")),
                reason=_md(segment.get("reason")),
            )
        )
    lines.extend(
        [
            "",
            f"- self_check.pass：{self_check.get('pass')}",
            f"- self_check.issues：{_format_list(self_check.get('issues'))}",
            f"- overall_rationale：{plan.get('overall_rationale', '')}",
            "",
            "## Validator 结果",
            "",
            f"- errors：{_format_list(validation.get('errors'))}",
            f"- warnings：{_format_list(validation.get('warnings'))}",
            "",
            "## 输出文件",
            "",
            f"- 高光视频：`{highlight_path}`",
            f"- 预览视频：`{preview_path if preview_path else '未生成'}`",
            f"- 结构化方案：`{segments_path}`",
            f"- 结果摘要：`{result_summary_path}`",
            f"- 运行日志：`{run_log_path}`",
            f"- 方案校验通过：{validation['ok']}",
        ]
    )
    write_text(report_path, "\n".join(lines) + "\n")


def run_skill(
    input_video: Path,
    instruction: str,
    target_duration: float = 30.0,
    output_dir: Path = Path("outputs"),
    llm_video_url: str | None = None,
    llm_backend: str | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    paths = _output_paths(output_dir, input_video)
    for key in ("videos", "reports", "logs", "work"):
        ensure_dir(paths[key])

    logger = setup_logger(paths["log"])
    logger.info("开始运行 ClawCut 视频高光剪辑 Skill")
    logger.info("启动参数：input_video=%s", input_video)
    logger.info("启动参数：instruction=%s", instruction)
    logger.info("启动参数：target_duration=%.3f", target_duration)
    logger.info("启动参数：output_dir=%s", output_dir)
    logger.info("启动参数：llm_backend=%s", llm_backend or "config/default.yaml")
    logger.info("启动参数：llm_video_url_present=%s", bool(llm_video_url))
    logger.info("启动参数：config=%s", config_path or "默认配置")
    logger.info("输入视频：%s", input_video)
    logger.info("本次输出目录：%s", paths["run"])
    logger.info("目标时长：%.3f 秒", target_duration)

    if not str(instruction or "").strip():
        raise SkillError("instruction 不能为空，请提供明确的剪辑目标")
    if not input_video.exists():
        raise SkillError(f"input_video 不存在：{input_video}")
    if not input_video.is_file():
        raise SkillError(f"input_video 不是文件：{input_video}")
    if float(target_duration) <= 0:
        raise SkillError("target_duration 必须为正数")

    config = load_config(config_path)
    if llm_backend:
        config.setdefault("llm", {})
        config["llm"]["backend"] = llm_backend
        logger.info("命令行覆盖 LLM backend：%s", llm_backend)
    if llm_video_url:
        config.setdefault("llm", {})
        config["llm"]["video_url"] = llm_video_url
        config["llm"]["video_input_mode"] = "url"
        logger.info("LLM 将使用外部视频 URL 输入：%s", _mask_url(llm_video_url))
    video_info = probe_video(input_video, config.get("ffmpeg", {}).get("probe_command", "ffprobe"), logger)
    logger.info("视频探测完成：%s", video_info)

    preview_path: Path | None = None
    if bool(config.get("preview", {}).get("enabled", True)):
        preview_path = make_preview(input_video, paths["preview"], config, logger)
        logger.info("预览视频已写入：%s", preview_path)
    else:
        logger.info("preview.enabled=false，跳过本地 preview 生成")

    if llm_video_url:
        model_video_input_source = "user_provided_url"
        model_video_input_path_or_url = llm_video_url
    elif str(config.get("llm", {}).get("video_url", "") or "").strip():
        model_video_input_source = "user_provided_url"
        model_video_input_path_or_url = str(config["llm"]["video_url"]).strip()
    else:
        if preview_path is None:
            raise SkillError("未提供 --llm_video_url，且 preview.enabled=false，无法准备模型视频输入")
        model_video_input_source = "local_preview"
        model_video_input_path_or_url = str(preview_path)
    logger.info("模型输入来源：%s", model_video_input_source)
    logger.info("模型输入路径或 URL：%s", _mask_url(model_video_input_path_or_url))
    logger.info("最终裁剪源：%s", input_video)
    logger.info("使用的 LLM backend：%s", config.get("llm", {}).get("backend", "mock"))

    client = create_llm_client(config, logger=logger)
    plan = client.generate_edit_plan(str(preview_path or ""), instruction, target_duration, video_info, config)
    logger.info("LLM 输出解析完成")
    plan = _attach_pipeline_metadata(
        plan,
        model_video_input_source,
        model_video_input_path_or_url,
        input_video,
        preview_path,
    )
    try:
        validation = assert_valid_plan(plan, video_info["duration"], target_duration, config)
    except SkillError as exc:
        llm_config = config.get("llm", {})
        used_backend = str(plan.get("llm_metadata", {}).get("backend", llm_config.get("backend", ""))).lower()
        if used_backend == "ark" and bool(llm_config.get("fallback_to_mock", True)):
            logger.warning("Ark LLM 输出校验失败，已回退到 mock：%s", exc)
            plan = MockLLMClient().generate_edit_plan(str(preview_path or ""), instruction, target_duration, video_info, config)
            plan = _attach_pipeline_metadata(
                plan,
                model_video_input_source,
                model_video_input_path_or_url,
                input_video,
                preview_path,
            )
            validation = assert_valid_plan(plan, video_info["duration"], target_duration, config)
        else:
            raise
    logger.info("剪辑方案校验完成：%s", validation)
    if validation.get("warnings"):
        logger.warning("validator warnings：%s", validation["warnings"])

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
        preview_path=preview_path,
        segments_path=paths["segments"],
        result_summary_path=paths["result_summary"],
        run_log_path=paths["log"],
    )
    logger.info("中文报告已写入：%s", paths["report"])
    _write_success_summary(
        paths["result_summary"],
        paths,
        input_video,
        instruction,
        target_duration,
        plan,
        validation,
        preview_path,
    )
    logger.info("结果摘要已写入：%s", paths["result_summary"])
    logger.info("Skill 运行完成")

    return {
        "highlight": str(paths["highlight"]),
        "segments": str(paths["segments"]),
        "report": str(paths["report"]),
        "result_summary": str(paths["result_summary"]),
        "log": str(paths["log"]),
        "output_dir": str(paths["run"]),
        "validation": validation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 ClawCut 视频高光剪辑 Skill。")
    parser.add_argument("--input_video", type=Path, required=True)
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--target_duration", type=float, default=30.0)
    parser.add_argument("--output_dir", type=Path, default=Path("outputs"))
    parser.add_argument(
        "--llm_backend",
        choices=["ark", "mock"],
        default=None,
        help="可选：覆盖 config/default.yaml 中的 llm.backend。",
    )
    parser.add_argument(
        "--llm_video_url",
        default=None,
        help="可选：提供给大模型的视频 URL，例如 TOS 公开或签名 URL。未提供时使用本地 preview 的 data URL。",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="可选：指定配置文件路径。未提供时使用 Skill 内置 config/default.yaml。",
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    paths = _output_paths(output_dir, args.input_video)
    for key in ("reports", "logs"):
        ensure_dir(paths[key])
    logger = setup_logger(paths["log"])

    try:
        result = run_skill(
            args.input_video,
            args.instruction,
            args.target_duration,
            output_dir,
            llm_video_url=args.llm_video_url,
            llm_backend=args.llm_backend,
            config_path=args.config,
        )
    except SkillError as exc:
        logger.error("%s", exc)
        logger.error(traceback.format_exc())
        _write_failure_summary(
            paths["result_summary"],
            paths,
            args.input_video,
            args.instruction,
            args.target_duration,
            exc.__class__.__name__,
            str(exc),
        )
        print(f"ClawCut 视频高光剪辑 Skill 运行失败：{exc}")
        print(f"结果摘要：{paths['result_summary']}")
        print(f"运行日志：{paths['log']}")
        return 1
    except Exception as exc:
        logger.error("发生未预期异常：%s", exc)
        logger.error(traceback.format_exc())
        _write_failure_summary(
            paths["result_summary"],
            paths,
            args.input_video,
            args.instruction,
            args.target_duration,
            exc.__class__.__name__,
            str(exc),
        )
        print(f"ClawCut 视频高光剪辑 Skill 运行失败：{exc}")
        print(f"结果摘要：{paths['result_summary']}")
        print(f"运行日志：{paths['log']}")
        return 1

    print("ClawCut 视频高光剪辑 Skill 运行完成。")
    print(f"输出目录：{result['output_dir']}")
    print(f"高光视频：{result['highlight']}")
    print(f"片段方案：{result['segments']}")
    print(f"中文报告：{result['report']}")
    print(f"结果摘要：{result['result_summary']}")
    print(f"运行日志：{result['log']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
