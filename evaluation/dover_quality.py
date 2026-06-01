from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DoverConfig:
    enabled: bool = False
    require_dover: bool = False
    repo_dir: Path | None = None
    python: str | None = None
    opt_path: Path | None = None
    device: str = "cpu"
    timeout_seconds: int = 300


def _round(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def build_dover_config(
    *,
    enabled: bool = False,
    require_dover: bool = False,
    repo_dir: Path | None = None,
    python: str | None = None,
    opt_path: Path | None = None,
    device: str | None = None,
    timeout_seconds: int | None = None,
) -> DoverConfig:
    return DoverConfig(
        enabled=enabled,
        require_dover=require_dover,
        repo_dir=repo_dir or _env_path("DOVER_REPO_DIR"),
        python=python or os.environ.get("DOVER_PYTHON") or sys.executable,
        opt_path=opt_path or _env_path("DOVER_OPT_PATH"),
        device=device or os.environ.get("DOVER_DEVICE") or "cpu",
        timeout_seconds=int(timeout_seconds or os.environ.get("DOVER_TIMEOUT_SECONDS") or 300),
    )


def _unavailable(message: str) -> dict[str, Any]:
    return {
        "provider": "DOVER",
        "status": "unavailable",
        "dover_status": "unavailable",
        "dover_error": message,
        "dover_model": "dover",
        "dover_device": None,
        "dover_runtime_seconds": None,
        "dover_fused_overall_score": None,
        "dover_raw_technical_score": None,
        "dover_raw_visual_aesthetic_score": None,
        "dover_reference_percentiles": None,
    }


def _failure(status: str, message: str, runtime_seconds: float | None = None) -> dict[str, Any]:
    return {
        "provider": "DOVER",
        "status": status,
        "dover_status": status,
        "dover_error": message,
        "dover_model": "dover",
        "dover_device": None,
        "dover_runtime_seconds": _round(runtime_seconds),
        "dover_fused_overall_score": None,
        "dover_raw_technical_score": None,
        "dover_raw_visual_aesthetic_score": None,
        "dover_reference_percentiles": None,
    }


def _normalize_success(payload: dict[str, Any], runtime_seconds: float, device: str) -> dict[str, Any]:
    return {
        "provider": "DOVER",
        "status": "success",
        "dover_status": "success",
        "dover_model": str(payload.get("dover_model", "dover")),
        "dover_device": str(payload.get("dover_device", device)),
        "dover_runtime_seconds": _round(payload.get("dover_runtime_seconds", runtime_seconds)),
        "dover_fused_overall_score": _round(payload.get("dover_fused_overall_score")),
        "dover_raw_technical_score": _round(payload.get("dover_raw_technical_score")),
        "dover_raw_visual_aesthetic_score": _round(payload.get("dover_raw_visual_aesthetic_score")),
        "dover_reference_percentiles": payload.get("dover_reference_percentiles"),
    }


def evaluate_dover_quality(video_path: Path, config: DoverConfig) -> dict[str, Any]:
    if not config.enabled:
        return {"provider": "DOVER", "status": "disabled", "dover_status": "disabled"}
    if not config.repo_dir or not config.repo_dir.exists():
        result = _unavailable("DOVER_REPO_DIR 未配置或目录不存在")
        if config.require_dover:
            result["dover_required_failed"] = True
        return result
    if config.opt_path is not None and not config.opt_path.exists():
        result = _unavailable("DOVER_OPT_PATH 不存在")
        if config.require_dover:
            result["dover_required_failed"] = True
        return result
    runner = Path(__file__).resolve().parent / "dover_runner.py"
    command = [
        str(config.python or sys.executable),
        str(runner),
        "--video_path",
        str(video_path),
        "--repo_dir",
        str(config.repo_dir),
        "--device",
        config.device,
    ]
    if config.opt_path:
        command.extend(["--opt_path", str(config.opt_path)])
    started = time.monotonic()
    try:
        process = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=config.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        result = _failure("timeout", f"DOVER 超时：{config.timeout_seconds} 秒", time.monotonic() - started)
        if config.require_dover:
            result["dover_required_failed"] = True
        return result
    runtime = time.monotonic() - started
    try:
        payload = json.loads(process.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        if process.returncode != 0:
            result = _failure("failed", process.stderr.strip() or process.stdout.strip() or "DOVER 退出码非零", runtime)
            if config.require_dover:
                result["dover_required_failed"] = True
            return result
        result = _failure("invalid_json", f"DOVER Runner 返回非法 JSON：{exc}", runtime)
        if config.require_dover:
            result["dover_required_failed"] = True
        return result
    if not isinstance(payload, dict):
        result = _failure("invalid_json", "DOVER Runner JSON 根节点不是 object", runtime)
        if config.require_dover:
            result["dover_required_failed"] = True
        return result
    if process.returncode != 0:
        status = str(payload.get("dover_status") or payload.get("status") or "failed")
        result = _failure(status, str(payload.get("dover_error") or process.stderr.strip() or "DOVER 退出码非零"), runtime)
        if config.require_dover:
            result["dover_required_failed"] = True
        return result
    return _normalize_success(payload, runtime, config.device)
