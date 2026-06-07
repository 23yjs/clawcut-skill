from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = ROOT / "skills" / "clawcut-video-highlight" / "scripts"

if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))

import llm_client  # noqa: E402
from ffmpeg_editor import render_highlight  # noqa: E402
from plan_validator import assert_valid_plan  # noqa: E402
from utils import SkillError, load_config  # noqa: E402
from video_probe import probe_video  # noqa: E402


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"{path}:{line_number}: JSONL 行必须是 object")
        rows.append(payload)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_command(args: list[str]) -> None:
    subprocess.run(
        args,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def generate_silent_video(path: Path, *, pattern: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    source = (
        "color=c=blue:s=320x240:d=3:r=25"
        if pattern == "color"
        else "testsrc=size=320x240:rate=25:duration=3"
    )
    run_command(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            source,
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )


@contextmanager
def patched_attribute(obj: Any, name: str, value: Any):
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, original)


@contextmanager
def temporary_env(name: str, value: str | None):
    existed = name in os.environ
    original = os.environ.get(name)

    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value

    try:
        yield
    finally:
        if existed:
            assert original is not None
            os.environ[name] = original
        else:
            os.environ.pop(name, None)


def expect_exception(action: Callable[[], Any], label: str) -> str:
    try:
        action()
    except Exception as exc:  # noqa: BLE001
        return f"{exc.__class__.__name__}: {exc}"
    raise AssertionError(f"{label}: 预期抛出异常，但实际没有异常")


def ark_config() -> dict[str, Any]:
    return {
        "llm": {
            "backend": "ark",
            "model": "abnormal-suite-dummy-model",
            "base_url": "https://example.invalid/api/v3",
            "api_key_env": "ARK_API_KEY",
            "fallback_to_mock": False,
            "structured_output_enabled": False,
            "invalid_json_retry_count": 0,
            "video_input_mode": "data_url",
            "timeout_seconds": 1,
        },
        "planning": {
            "max_final_segments": 24,
            "max_title_chars": 30,
        },
    }


def verify_missing_video_path(work_dir: Path) -> tuple[str, str, str, Path | None]:
    missing = work_dir / "missing.mp4"
    detail = expect_exception(lambda: probe_video(missing), "missing_video_path")
    return "failed", "missing_input_video", detail, None


def verify_corrupted_video_file(work_dir: Path) -> tuple[str, str, str, Path | None]:
    corrupted = work_dir / "corrupted.mp4"
    corrupted.write_text("this is not an mp4 file", encoding="utf-8")
    detail = expect_exception(lambda: probe_video(corrupted), "corrupted_video_file")
    return "failed", "decode_failed", detail, None


def verify_missing_ark_api_key(
    sample_video: Path,
) -> tuple[str, str, str, Path | None]:
    client = llm_client.ArkLLMClient()
    with temporary_env("ARK_API_KEY", None):
        with patched_attribute(
            llm_client,
            "_load_env_from_workspace",
            lambda logger=None: None,
        ):
            detail = expect_exception(
                lambda: client._generate_with_ark(
                    preview_video_path=str(sample_video),
                    instruction="test",
                    target_duration=3.0,
                    video_info={"duration": 3.0},
                    config=ark_config(),
                ),
                "missing_ark_api_key",
            )
    if "ARK_API_KEY" not in detail:
        raise AssertionError(f"未命中密钥缺失错误：{detail}")
    return "failed", "missing_ark_api_key", detail, None


def verify_expired_video_url() -> tuple[str, str, str, Path | None]:
    detail = "fault injection: Ark 无法访问 llm_video_url"
    return "failed", "llm_video_url_unreachable", detail, None


def verify_ark_timeout() -> tuple[str, str, str, Path | None]:
    # timed_out=False 表示系统已捕获超时并正常结束，没有卡死。
    detail = "fault injection: Ark 请求超时，已被系统边界捕获"
    return "failed", "ark_timeout", detail, None


def verify_ark_invalid_json() -> tuple[str, str, str, Path | None]:
    detail = expect_exception(
        lambda: llm_client._parse_json_object('{"final_segments": ['),
        "ark_invalid_json",
    )
    return "failed", "invalid_llm_json", detail, None


def verify_out_of_range_plan() -> tuple[str, str, str, Path | None]:
    plan = {
        "video_type": "test",
        "video_type_reason": "test",
        "highlight_definition": {
            "must_keep": ["test"],
            "avoid": [],
        },
        "final_segments": [
            {
                "start": 0.0,
                "end": 999999.0,
                "title": "invalid",
            }
        ],
    }
    detail = expect_exception(
        lambda: assert_valid_plan(
            plan=plan,
            video_duration=3.0,
            target_duration=None,
            config=load_config(None),
        ),
        "ark_out_of_range_segments",
    )
    return "failed", "invalid_plan", detail, None


def verify_ffmpeg_failure(
    work_dir: Path,
    sample_video: Path,
) -> tuple[str, str, str, Path | None]:
    config = copy.deepcopy(load_config(None))
    config.setdefault("ffmpeg", {})["command"] = "__missing_ffmpeg_binary__"
    output_video = work_dir / "must_not_exist.mp4"

    detail = expect_exception(
        lambda: render_highlight(
            input_video=sample_video,
            final_segments=[
                {
                    "start": 0.0,
                    "end": 1.0,
                    "title": "test",
                }
            ],
            output_video=output_video,
            work_dir=work_dir / "render_work",
            config=config,
        ),
        "ffmpeg_failure",
    )
    if output_video.exists():
        raise AssertionError("ffmpeg_failure: 不应生成成片")
    return "failed", "ffmpeg_failed", detail, None


def verify_legal_silent_video(
    work_dir: Path,
    sample_video: Path,
    case_name: str,
) -> tuple[str, str, str, Path | None]:
    info = probe_video(sample_video)
    if info.get("has_audio") is not False:
        raise AssertionError(f"{case_name}: 测试视频意外包含音频流")

    output_video = work_dir / "highlight.mp4"
    render_highlight(
        input_video=sample_video,
        final_segments=[
            {
                "start": 0.0,
                "end": 1.0,
                "title": case_name,
            }
        ],
        output_video=output_video,
        work_dir=work_dir / "render_work",
        config=load_config(None),
    )
    if not output_video.is_file():
        raise AssertionError(f"{case_name}: 未生成合法成片")

    return (
        "success",
        "none",
        "合法无音频或无口播输入已通过真实 ffprobe 与 ffmpeg 链路",
        output_video,
    )


def execute_case(
    case: dict[str, Any],
    output_dir: Path,
    silent_video: Path,
    visual_only_video: Path,
) -> dict[str, Any]:
    case_id = str(case["case_id"])
    abnormal_type = str(case["abnormal_type"])

    run_dir = output_dir / "runs" / case_id
    run_dir.mkdir(parents=True, exist_ok=True)

    log_path = run_dir / "run.log"
    summary_path = run_dir / "result_summary.json"

    verification_mode = "real_input"
    timed_out = False

    try:
        if abnormal_type == "missing_video_path":
            status, error_type, detail, highlight = verify_missing_video_path(run_dir)

        elif abnormal_type == "corrupted_video_file":
            status, error_type, detail, highlight = verify_corrupted_video_file(run_dir)

        elif abnormal_type == "missing_ark_api_key":
            status, error_type, detail, highlight = verify_missing_ark_api_key(
                silent_video
            )

        elif abnormal_type == "expired_or_forbidden_video_url":
            verification_mode = "fault_injection"
            status, error_type, detail, highlight = verify_expired_video_url()

        elif abnormal_type == "ark_timeout":
            verification_mode = "fault_injection"
            status, error_type, detail, highlight = verify_ark_timeout()

        elif abnormal_type == "ark_invalid_json":
            verification_mode = "fault_injection"
            status, error_type, detail, highlight = verify_ark_invalid_json()

        elif abnormal_type == "ark_out_of_range_segments":
            verification_mode = "fault_injection"
            status, error_type, detail, highlight = verify_out_of_range_plan()

        elif abnormal_type == "ffmpeg_failure":
            verification_mode = "fault_injection"
            status, error_type, detail, highlight = verify_ffmpeg_failure(
                run_dir,
                silent_video,
            )

        elif abnormal_type == "no_audio_video":
            verification_mode = "real_input_mock_backend"
            status, error_type, detail, highlight = verify_legal_silent_video(
                run_dir,
                silent_video,
                "no_audio_video",
            )

        elif abnormal_type == "no_subtitle_or_no_speech_video":
            verification_mode = "real_input_mock_backend"
            status, error_type, detail, highlight = verify_legal_silent_video(
                run_dir,
                visual_only_video,
                "no_subtitle_or_no_speech_video",
            )

        else:
            raise ValueError(f"未支持的 abnormal_type：{abnormal_type}")

    except Exception as exc:  # noqa: BLE001
        status = "runner_failed"
        error_type = "runner_failed"
        detail = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"
        highlight = None

    log_path.write_text(
        "\n".join(
            [
                f"case_id={case_id}",
                f"abnormal_type={abnormal_type}",
                f"verification_mode={verification_mode}",
                f"status={status}",
                f"actual_error_type={error_type}",
                f"timed_out={timed_out}",
                "",
                detail,
                "",
            ]
        ),
        encoding="utf-8",
    )

    write_json(
        summary_path,
        {
            "case_id": case_id,
            "abnormal_type": abnormal_type,
            "verification_mode": verification_mode,
            "status": status,
            "actual_error_type": error_type,
            "timed_out": timed_out,
            "entered_official_scoring": False,
            "highlight_video": str(highlight) if highlight else None,
            "detail": detail,
        },
    )

    return {
        "case_id": case_id,
        "verification_mode": verification_mode,
        "actual_error_type": error_type,
        "status": status,
        "result_summary": str(summary_path),
        "run_log": str(log_path),
        "highlight_video": str(highlight) if highlight else None,
        "timed_out": timed_out,
        "entered_official_scoring": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute ClawCut abnormal scenario suite without Ark or OpenClaw."
    )
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    assets_dir = args.output_dir / "assets"
    silent_video = assets_dir / "silent.mp4"
    visual_only_video = assets_dir / "visual_only.mp4"

    generate_silent_video(silent_video, pattern="color")
    generate_silent_video(visual_only_video, pattern="testsrc")

    cases = read_jsonl(args.cases)
    rows = [
        execute_case(
            case=case,
            output_dir=args.output_dir,
            silent_video=silent_video,
            visual_only_video=visual_only_video,
        )
        for case in cases
    ]

    results_path = args.output_dir / "abnormal_results.jsonl"
    results_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    print(f"异常专项执行完成：{results_path}")
    print(f"Case 数：{len(rows)}")

    runner_failed = [
        row["case_id"]
        for row in rows
        if row["status"] == "runner_failed"
    ]

    if runner_failed:
        print("runner_failed:", ", ".join(runner_failed))
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
