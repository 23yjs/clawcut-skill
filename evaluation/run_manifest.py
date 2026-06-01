from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def sha256_file(path: Path | None) -> str | None:
    if not path or not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sanitize_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def git_commit_hash(cwd: Path) -> str | None:
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if process.returncode != 0:
        return None
    return process.stdout.strip()


def build_run_manifest(
    *,
    run_id: str,
    repo_root: Path,
    input_video_path: Path,
    gt_path: Path | None,
    instruction: str,
    target_duration: float | None,
    duration_policy_mode: str | None,
    skill_prompt_version: str | None,
    resolver_prompt_version: str | None,
    aesthetic_judge_prompt_version: str | None,
    skill_model: str | None,
    resolver_model: str | None,
    aesthetic_judge_model: str | None,
    skill_backend_requested: str | None,
    skill_backend_used: str | None,
    fallback_used: bool | None,
    generated_case_path: Path | None,
    segments_json_path: Path | None,
    highlight_video_path: Path | None,
    judge_repeats: int | None,
    judge_video_url: str | None,
    judge_video_upload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sanitized_judge_url = sanitize_url(judge_video_url)
    judge_video_upload = judge_video_upload or {}
    return {
        "run_id": run_id,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "git_commit_hash": git_commit_hash(repo_root),
        "input_video_path": str(input_video_path),
        "input_video_sha256": sha256_file(input_video_path),
        "gt_path": str(gt_path) if gt_path else None,
        "gt_sha256": sha256_file(gt_path),
        "instruction": instruction,
        "target_duration": target_duration,
        "duration_policy_mode": duration_policy_mode,
        "skill_prompt_version": skill_prompt_version,
        "resolver_prompt_version": resolver_prompt_version,
        "aesthetic_judge_prompt_version": aesthetic_judge_prompt_version,
        "skill_model": skill_model,
        "resolver_model": resolver_model,
        "aesthetic_judge_model": aesthetic_judge_model,
        "skill_backend_requested": skill_backend_requested,
        "skill_backend_used": skill_backend_used,
        "fallback_used": fallback_used,
        "generated_case_path": str(generated_case_path) if generated_case_path else None,
        "generated_case_sha256": sha256_file(generated_case_path),
        "segments_json_path": str(segments_json_path) if segments_json_path else None,
        "segments_json_sha256": sha256_file(segments_json_path),
        "highlight_video_path": str(highlight_video_path) if highlight_video_path else None,
        "highlight_video_sha256": sha256_file(highlight_video_path),
        "judge_repeats": judge_repeats,
        "judge_video_url_sanitized": sanitized_judge_url,
        "judge_video_url_sha256": sha256_text(judge_video_url),
        "judge_video_upload_status": judge_video_upload.get("upload_status") or judge_video_upload.get("status"),
        "judge_video_upload_bucket": judge_video_upload.get("bucket"),
        "judge_video_upload_object_key": judge_video_upload.get("object_key"),
        "judge_video_upload_url_sanitized": judge_video_upload.get("judge_video_url_sanitized"),
        "judge_video_upload_url_sha256": judge_video_upload.get("judge_video_url_sha256"),
    }


def write_run_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
