from __future__ import annotations

import importlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .run_manifest import sanitize_url, sha256_file, sha256_text
except ImportError:  # pragma: no cover - script mode
    from run_manifest import sanitize_url, sha256_file, sha256_text


@dataclass(frozen=True)
class TosUploadConfig:
    enabled: bool = False
    bucket: str | None = None
    region: str = "cn-beijing"
    endpoint: str = "tos-cn-beijing.volces.com"
    key_prefix: str = "output"
    presign_expires_seconds: int = 86400
    access_key_env: str = "TOS_ACCESS_KEY"
    secret_key_env: str = "TOS_SECRET_KEY"


def build_tos_upload_config(
    *,
    enabled: bool = False,
    bucket: str | None = None,
    region: str | None = None,
    endpoint: str | None = None,
    key_prefix: str | None = None,
    presign_expires_seconds: int | None = None,
    access_key_env: str | None = None,
    secret_key_env: str | None = None,
) -> TosUploadConfig:
    return TosUploadConfig(
        enabled=enabled,
        bucket=bucket or os.environ.get("TOS_BUCKET") or "clawcut",
        region=region or os.environ.get("TOS_REGION") or "cn-beijing",
        endpoint=endpoint or os.environ.get("TOS_ENDPOINT") or "tos-cn-beijing.volces.com",
        key_prefix=key_prefix or os.environ.get("TOS_KEY_PREFIX") or "output",
        presign_expires_seconds=int(
            presign_expires_seconds
            or os.environ.get("TOS_PRESIGN_EXPIRES_SECONDS")
            or 86400
        ),
        access_key_env=access_key_env or os.environ.get("TOS_ACCESS_KEY_ENV") or "TOS_ACCESS_KEY",
        secret_key_env=secret_key_env or os.environ.get("TOS_SECRET_KEY_ENV") or "TOS_SECRET_KEY",
    )


def instruction_fingerprint(
    *,
    video_id: str,
    instruction: str,
    target_duration: float | None,
) -> str:
    payload = {
        "video_id": video_id,
        "instruction": instruction,
        "target_duration": target_duration,
    }
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))[:12]


def build_tos_object_key(
    *,
    key_prefix: str,
    video_id: str,
    instruction: str,
    target_duration: float | None,
    run_id: str,
) -> str:
    prefix = key_prefix.strip("/") or "output"
    video_part = _safe_key_part(video_id, fallback="video")
    run_part = _safe_key_part(run_id, fallback="run")
    instruction_part = f"instruction-{instruction_fingerprint(video_id=video_id, instruction=instruction, target_duration=target_duration)}"
    return f"{prefix}/{video_part}/{instruction_part}/{run_part}/highlight.mp4"


def upload_judge_video_to_tos(
    *,
    video_path: Path,
    video_id: str,
    instruction: str,
    target_duration: float | None,
    run_id: str,
    config: TosUploadConfig,
) -> tuple[dict[str, Any], str | None]:
    object_key = build_tos_object_key(
        key_prefix=config.key_prefix,
        video_id=video_id,
        instruction=instruction,
        target_duration=target_duration,
        run_id=run_id,
    )
    base_record: dict[str, Any] = {
        "provider": "tos",
        "status": "disabled" if not config.enabled else "pending",
        "upload_status": "disabled" if not config.enabled else "pending",
        "bucket": config.bucket,
        "region": config.region,
        "endpoint": config.endpoint,
        "key_prefix": config.key_prefix,
        "object_key": object_key,
        "local_video_path": str(video_path),
        "local_video_sha256": sha256_file(video_path),
        "presign_expires_seconds": config.presign_expires_seconds,
        "judge_video_url_sanitized": None,
        "judge_video_url_sha256": None,
        "signed_url_present": False,
    }
    if not config.enabled:
        return base_record, None
    if not video_path.exists():
        return _failure(base_record, "HighlightVideoMissing", f"highlight.mp4 不存在：{video_path}"), None
    ak = os.environ.get(config.access_key_env)
    sk = os.environ.get(config.secret_key_env)
    if not ak or not sk:
        return _failure(
            base_record,
            "MissingTosCredentials",
            f"缺少 {config.access_key_env} 或 {config.secret_key_env} 环境变量",
        ), None
    if not config.bucket:
        return _failure(base_record, "MissingTosBucket", "缺少 TOS bucket 配置"), None

    try:
        tos = importlib.import_module("tos")
    except Exception as exc:
        return _failure(base_record, exc.__class__.__name__, "未安装或无法导入 TOS Python SDK：tos"), None

    try:
        client = tos.TosClientV2(ak, sk, config.endpoint, config.region)
        _put_object(client, bucket=config.bucket, key=object_key, video_path=video_path)
        signed_url = _pre_signed_get_url(
            tos,
            client,
            bucket=config.bucket,
            key=object_key,
            expires=config.presign_expires_seconds,
        )
    except Exception as exc:
        return _failure(base_record, exc.__class__.__name__, _safe_exception_message(exc)), None

    base_record.update(
        {
            "status": "success",
            "upload_status": "success",
            "judge_video_url_sanitized": sanitize_url(signed_url),
            "judge_video_url_sha256": sha256_text(signed_url),
            "signed_url_present": True,
        }
    )
    return base_record, signed_url


def _safe_key_part(value: str, *, fallback: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip()).strip("-._")
    return safe or fallback


def _failure(record: dict[str, Any], error_type: str, error_message: str) -> dict[str, Any]:
    failed = dict(record)
    failed.update(
        {
            "status": "failed",
            "upload_status": "failed",
            "error_type": error_type,
            "error_message": error_message,
            "signed_url_present": False,
        }
    )
    return failed


def _put_object(client: Any, *, bucket: str, key: str, video_path: Path) -> None:
    if hasattr(client, "put_object_from_file"):
        client.put_object_from_file(bucket, key, str(video_path))
        return
    if hasattr(client, "upload_file"):
        client.upload_file(bucket, key, str(video_path))
        return
    with video_path.open("rb") as handle:
        client.put_object(bucket, key, content=handle)


def _pre_signed_get_url(tos_module: Any, client: Any, *, bucket: str, key: str, expires: int) -> str:
    method_type = tos_module.HttpMethodType.Http_Method_Get
    attempts = (
        lambda: client.pre_signed_url(method_type, bucket=bucket, key=key, expires=expires),
        lambda: client.pre_signed_url(method_type, bucket_name=bucket, key=key, expires=expires),
        lambda: client.pre_signed_url(method_type, bucket, key, expires=expires),
    )
    last_error: Exception | None = None
    for attempt in attempts:
        try:
            return _extract_signed_url(attempt())
        except TypeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise RuntimeError("无法生成 TOS 预签名 URL")


def _extract_signed_url(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, dict) and output.get("signed_url"):
        return str(output["signed_url"])
    signed_url = getattr(output, "signed_url", None)
    if signed_url:
        return str(signed_url)
    raise RuntimeError("TOS pre_signed_url 未返回 signed_url")


def _safe_exception_message(exc: Exception) -> str:
    message = str(exc) or exc.__class__.__name__
    return re.sub(r"https?://\S+", lambda match: sanitize_url(match.group(0)) or "[url]", message)
