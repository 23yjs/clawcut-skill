from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 根节点必须是 object：{path}")
    return payload


def _same_path(left: Any, right: Path) -> bool:
    if not left:
        return False
    try:
        left_path = Path(str(left))
        if left_path.exists() and right.exists():
            return left_path.resolve() == right.resolve()
    except OSError:
        pass
    return str(left).strip() == str(right)


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _input_video_match(
    *,
    result_summary: dict[str, Any],
    input_video: Path,
    warnings: list[str],
) -> tuple[bool, str]:
    summary_input = result_summary.get("input_video")
    if _same_path(summary_input, input_video):
        return True, "path"

    summary_hashes = {
        str(value).strip().lower()
        for value in (
            result_summary.get("input_video_sha256"),
            result_summary.get("final_edit_source_sha256"),
        )
        if str(value or "").strip()
    }
    if not summary_hashes:
        return False, "legacy_path"

    current_hash = _sha256_file(input_video)
    if current_hash and current_hash.lower() in summary_hashes:
        warnings.append("input_video 路径不同，但文件 SHA-256 一致")
        return True, "sha256"

    return False, "mismatch"


def _target_duration_matches(actual: Any, expected: float | None) -> bool:
    if expected is None:
        return actual in (None, "")
    try:
        return abs(float(actual) - float(expected)) <= 0.001
    except (TypeError, ValueError):
        return False


def _extract_backend_from_log(run_log: Path) -> tuple[str | None, bool]:
    if not run_log.exists():
        return None, False
    text = run_log.read_text(encoding="utf-8", errors="ignore")
    fallback_used = bool(re.search(r"回退到\s*mock|fallback", text, flags=re.IGNORECASE))
    backend = None
    match = re.search(r"使用的 LLM backend[:：]\s*([a-zA-Z0-9_-]+)", text)
    if match:
        backend = match.group(1).strip().lower()
    if fallback_used:
        backend = "mock"
    return backend, fallback_used


def find_skill_artifacts(skill_output_dir: Path, input_video: Path) -> dict[str, Path]:
    direct_root = skill_output_dir
    nested_root = skill_output_dir / Path(input_video).stem
    root = direct_root if (direct_root / "reports" / "segments.json").exists() else nested_root
    return {
        "run_dir": root,
        "result_summary": root / "reports" / "result_summary.json",
        "segments_json": root / "reports" / "segments.json",
        "highlight_video": root / "videos" / "highlight.mp4",
        "run_log": root / "logs" / "run.log",
    }


def validate_skill_artifacts(
    *,
    input_video: Path,
    instruction: str,
    target_duration: float | None,
    skill_output_dir: Path,
) -> dict[str, Any]:
    paths = find_skill_artifacts(skill_output_dir, input_video)
    errors: list[str] = []
    warnings: list[str] = []
    result_summary: dict[str, Any] = {}
    segments_payload: dict[str, Any] = {}

    result_summary_exists = paths["result_summary"].exists()
    segments_json_exists = paths["segments_json"].exists()
    highlight_video_exists = paths["highlight_video"].exists()
    run_log_exists = paths["run_log"].exists()

    if not result_summary_exists:
        errors.append(f"缺少 result_summary.json：{paths['result_summary']}")
    else:
        try:
            result_summary = _read_json(paths["result_summary"])
        except Exception as exc:
            errors.append(f"result_summary.json 读取失败：{exc}")

    if not segments_json_exists:
        errors.append(f"缺少 segments.json：{paths['segments_json']}")
    else:
        try:
            segments_payload = _read_json(paths["segments_json"])
        except Exception as exc:
            errors.append(f"segments.json 读取失败：{exc}")

    if not highlight_video_exists:
        errors.append(f"缺少 highlight.mp4：{paths['highlight_video']}")
    if not run_log_exists:
        errors.append(f"缺少 run.log：{paths['run_log']}")

    result_summary_status = result_summary.get("status")
    if result_summary and result_summary_status != "success":
        errors.append(f"result_summary.status 不是 success：{result_summary_status}")

    input_video_match = False
    input_video_match_method = "mismatch"
    if result_summary:
        input_video_match, input_video_match_method = _input_video_match(
            result_summary=result_summary,
            input_video=input_video,
            warnings=warnings,
        )
    if result_summary and not input_video_match:
        errors.append("result_summary.input_video 与当前 input_video 不一致")

    instruction_match = result_summary.get("instruction") == instruction if result_summary else False
    if result_summary and not instruction_match:
        errors.append("result_summary.instruction 与当前 instruction 不一致")

    target_duration_match = _target_duration_matches(result_summary.get("target_duration"), target_duration) if result_summary else False
    if result_summary and not target_duration_match:
        errors.append("result_summary.target_duration 与当前 target_duration 不一致")

    segments_json_match = _same_path(result_summary.get("segments_json"), paths["segments_json"]) if result_summary else False
    if result_summary and not segments_json_match:
        errors.append("result_summary.segments_json 与实际读取文件不一致")

    requested = str(result_summary.get("skill_backend_requested") or "").strip().lower()
    used = str(result_summary.get("skill_backend_used") or "").strip().lower()
    fallback_used = bool(result_summary.get("fallback_used", False))
    log_backend, log_fallback = _extract_backend_from_log(paths["run_log"])
    if not requested and log_backend:
        requested = log_backend
        warnings.append("result_summary 缺少 skill_backend_requested，已从 run.log 兜底推断。")
    if not used and log_backend:
        used = log_backend
        warnings.append("result_summary 缺少 skill_backend_used，已从 run.log 兜底推断。")
    fallback_used = fallback_used or log_fallback
    if not requested:
        requested = "unknown"
    if not used:
        used = "unknown"

    source_video_duration = result_summary.get("source_video_duration")
    if source_video_duration is None and isinstance(segments_payload.get("duration_policy"), dict):
        source_video_duration = segments_payload.get("duration_policy", {}).get("allowed_max_duration")

    return {
        "artifact_validation_passed": not errors,
        "skill_backend_requested": requested,
        "skill_backend_used": used,
        "fallback_used": fallback_used,
        "result_summary_status": result_summary_status,
        "highlight_video_exists": highlight_video_exists,
        "segments_json_exists": segments_json_exists,
        "result_summary_exists": result_summary_exists,
        "run_log_exists": run_log_exists,
        "input_video_match": input_video_match,
        "input_video_match_method": input_video_match_method,
        "instruction_match": instruction_match,
        "target_duration_match": target_duration_match,
        "segments_json_match": segments_json_match,
        "artifact_validation_errors": errors,
        "artifact_validation_warnings": warnings,
        "paths": {key: str(value) for key, value in paths.items()},
        "result_summary": result_summary,
        "segments_payload": segments_payload,
        "source_video_duration": source_video_duration,
        "skill_prompt_version": result_summary.get("skill_prompt_version"),
        "skill_model": result_summary.get("skill_model"),
        "run_started_at": result_summary.get("run_started_at"),
        "run_finished_at": result_summary.get("run_finished_at"),
    }
