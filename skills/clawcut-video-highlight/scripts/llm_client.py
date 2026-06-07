from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llm_prompts import SYSTEM_PROMPT, build_strict_json_edit_prompt, compact_edit_plan_json_schema
from mock_llm import generate_mock_plan
from utils import SkillError, require_file


class BaseLLMClient(ABC):
    @abstractmethod
    def generate_edit_plan(
        self,
        preview_video_path: str,
        instruction: str,
        target_duration: float,
        video_info: dict,
        config: dict,
    ) -> dict:
        raise NotImplementedError


class MockLLMClient(BaseLLMClient):
    def generate_edit_plan(
        self,
        preview_video_path: str,
        instruction: str,
        target_duration: float,
        video_info: dict,
        config: dict,
    ) -> dict:
        plan = generate_mock_plan(video_info, instruction, target_duration, config)
        plan.setdefault("llm_metadata", {})
        plan["llm_metadata"].update(
            {
                "backend": "mock",
                "preview_video_path": preview_video_path,
            }
        )
        return plan


class ArkLLMClient(BaseLLMClient):
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("clawcut-video-highlight")
        self.mock_client = MockLLMClient()

    def generate_edit_plan(
        self,
        preview_video_path: str,
        instruction: str,
        target_duration: float,
        video_info: dict,
        config: dict,
    ) -> dict:
        llm_config = config.get("llm", {})
        fallback_to_mock = bool(llm_config.get("fallback_to_mock", True))

        try:
            return self._generate_with_ark(
                preview_video_path=preview_video_path,
                instruction=instruction,
                target_duration=target_duration,
                video_info=video_info,
                config=config,
            )
        except SkillError as exc:
            if not fallback_to_mock:
                raise
            self.logger.warning("Ark LLM 调用不可用，已回退到 mock：%s", exc)
            return self.mock_client.generate_edit_plan(
                preview_video_path,
                instruction,
                target_duration,
                video_info,
                config,
            )

    def _generate_with_ark(
        self,
        preview_video_path: str,
        instruction: str,
        target_duration: float,
        video_info: dict,
        config: dict,
    ) -> dict:
        _load_env_from_workspace(logger=self.logger)
        llm_config = config.get("llm", {})
        model = str(llm_config.get("model", "") or os.environ.get("ARK_MODEL", "")).strip()
        base_url = str(llm_config.get("base_url", "") or os.environ.get("ARK_BASE_URL", "")).strip()
        api_key_env = str(llm_config.get("api_key_env", "ARK_API_KEY") or "ARK_API_KEY")
        api_key = os.environ.get(api_key_env, "").strip()

        missing = []
        if not model:
            missing.append("llm.model")
        if not base_url:
            missing.append("llm.base_url")
        if not api_key:
            missing.append(api_key_env)
        if missing:
            raise SkillError("Ark LLM 配置缺失：" + ", ".join(missing))

        video_url, video_source, preview_size_mb = _resolve_video_input(preview_video_path, llm_config)
        planning_config = config.get("planning") if isinstance(config.get("planning"), dict) else {}
        max_final_segments = int(planning_config.get("max_final_segments", 24))
        max_title_chars = int(planning_config.get("max_title_chars", 30))
        prompt = build_strict_json_edit_prompt(
            video_info,
            instruction,
            target_duration,
            duration_policy=config.get("duration_policy"),
            max_final_segments=max_final_segments,
            max_title_chars=max_title_chars,
        )
        request_url = _resolve_chat_completions_url(base_url)
        video_payload = {
            "url": video_url,
        }
        video_fps = llm_config.get("video_fps")
        if video_fps not in (None, ""):
            video_payload["fps"] = float(video_fps)

        payload = {
            "model": model,
            "temperature": float(llm_config.get("temperature", 0.2)),
            "max_tokens": int(llm_config.get("max_tokens", 8192)),
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt,
                        },
                        {
                            "type": "video_url",
                            "video_url": video_payload,
                        },
                    ],
                },
            ],
        }
        structured_output_enabled = bool(llm_config.get("structured_output_enabled", True))
        structured_output_mode = str(llm_config.get("structured_output_mode", "json_schema") or "json_schema")
        if structured_output_enabled:
            _apply_response_format(
                payload,
                structured_output_mode=structured_output_mode,
                max_final_segments=max_final_segments,
                max_title_chars=max_title_chars,
            )
        timeout = float(llm_config.get("timeout_seconds", 120))
        self.logger.info(
            "正在调用 Ark LLM：model=%s url=%s video_source=%s preview_size=%.2fMB",
            model,
            request_url,
            video_source,
            preview_size_mb,
        )
        diagnostics_dir = _diagnostics_dir(config)
        invalid_json_retry_count = int(llm_config.get("invalid_json_retry_count", 1))
        attempts: list[dict[str, Any]] = []
        structured_fallback_used = False
        last_error: SkillError | None = None
        for attempt_index in range(1, invalid_json_retry_count + 2):
            attempt_payload = json.loads(json.dumps(payload, ensure_ascii=False))
            if attempt_index > 1:
                _set_repair_prompt(
                    attempt_payload,
                    video_info=video_info,
                    instruction=instruction,
                    target_duration=target_duration,
                    duration_policy=config.get("duration_policy"),
                    max_final_segments=max_final_segments,
                    max_title_chars=max_title_chars,
                )
            try:
                raw_response, attempt_diag = _post_and_diagnose(
                    request_url=request_url,
                    api_key=api_key,
                    payload=attempt_payload,
                    timeout=timeout,
                    attempt_index=attempt_index,
                    structured_output_mode=structured_output_mode if attempt_payload.get("response_format") else "none",
                )
            except SkillError as exc:
                if (
                    structured_output_enabled
                    and not structured_fallback_used
                    and _looks_like_structured_output_unsupported(str(exc))
                ):
                    structured_fallback_used = True
                    payload.pop("response_format", None)
                    self.logger.warning("Ark 端不支持 structured response_format，已回退普通 JSON prompt：%s", exc)
                    continue
                raise
            content = attempt_diag.get("response_text", "")
            try:
                plan = _parse_json_object(str(content))
            except SkillError as exc:
                attempt_diag["json_parse_success"] = False
                attempt_diag["json_parse_error"] = str(exc)
                attempt_diag["truncated_suspected"] = _truncated_suspected(attempt_diag, str(content))
                attempts.append(_public_attempt_diagnostics(attempt_diag))
                _write_ark_response_diagnostics(diagnostics_dir, attempts, str(content))
                last_error = exc
                if attempt_index <= invalid_json_retry_count:
                    self.logger.warning("Ark 输出不是合法 JSON，执行第 %s 次修复重试", attempt_index)
                    continue
                raise SkillError("模型输出不是合法 JSON，已保存 Ark 响应诊断") from exc
            attempt_diag["json_parse_success"] = True
            attempt_diag["json_parse_error"] = None
            attempt_diag["truncated_suspected"] = False
            attempts.append(_public_attempt_diagnostics(attempt_diag))
            usage = _sum_usage([item.get("usage") for item in attempts if isinstance(item.get("usage"), dict)])
            latency_seconds = _sum_number([item.get("latency_seconds") for item in attempts])
            request_started_at = attempts[0].get("request_started_at")
            request_finished_at = attempts[-1].get("request_finished_at")
            finish_reason = attempts[-1].get("finish_reason")
            break
        else:  # pragma: no cover
            raise last_error or SkillError("Ark LLM 调用失败")
        plan.setdefault("llm_metadata", {})
        plan["llm_metadata"].update(
            {
                "backend": "ark",
                "model": model,
                "preview_video_path": preview_video_path,
                "video_source": video_source,
                "structured_output_mode": structured_output_mode if structured_output_enabled else "none",
                "structured_output_fallback_used": structured_fallback_used,
                "finish_reason": finish_reason,
                "response_char_count": attempts[-1].get("response_char_count") if attempts else None,
                "ark_attempts": attempts,
                "attempt_count": len(attempts),
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                },
                "request_started_at": request_started_at,
                "request_finished_at": request_finished_at,
                "latency_seconds": round(float(latency_seconds), 3) if latency_seconds is not None else None,
            }
        )
        return plan


def create_llm_client(config: dict, logger: logging.Logger | None = None) -> BaseLLMClient:
    backend = str(config.get("llm", {}).get("backend", "mock") or "mock").strip().lower()
    if backend == "mock":
        return MockLLMClient()
    if backend == "ark":
        return ArkLLMClient(logger=logger)
    raise SkillError(f"不支持的 LLM backend：{backend}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _candidate_env_paths() -> list[Path]:
    candidates: list[Path] = []
    for base in (Path.cwd(), Path(__file__).resolve()):
        current = base if base.is_dir() else base.parent
        for directory in (current, *current.parents):
            env_path = directory / ".env"
            if env_path not in candidates:
                candidates.append(env_path)
    return candidates


def _parse_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_env_file(path: Path) -> list[str]:
    loaded_keys: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key.startswith("#") or key in os.environ:
            continue
        os.environ[key] = _parse_env_value(value)
        loaded_keys.append(key)
    return loaded_keys


def _load_env_from_workspace(logger: logging.Logger | None = None) -> None:
    for env_path in _candidate_env_paths():
        if not env_path.is_file():
            continue
        loaded_keys = _load_env_file(env_path)
        if loaded_keys and logger:
            safe_keys = ", ".join(sorted(loaded_keys))
            logger.info("已从 %s 加载环境变量：%s", env_path, safe_keys)
        return


def _encode_video_as_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(str(path))[0] or "video/mp4"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _resolve_video_input(preview_video_path: str, llm_config: dict[str, Any]) -> tuple[str, str, float]:
    mode = str(llm_config.get("video_input_mode", "auto") or "auto").strip().lower()
    configured_url = str(llm_config.get("video_url", "") or "").strip()
    if mode not in {"auto", "url", "data_url"}:
        raise SkillError(f"不支持的 llm.video_input_mode：{mode}")

    if configured_url and mode in {"auto", "url"}:
        preview_path = Path(preview_video_path)
        preview_size_mb = preview_path.stat().st_size / 1024 / 1024 if preview_path.exists() else 0.0
        return configured_url, "url", preview_size_mb

    if mode == "url":
        raise SkillError("llm.video_input_mode=url 时必须配置 llm.video_url 或使用 --llm_video_url")

    preview_path = Path(preview_video_path)
    require_file(preview_path, "预览视频")
    preview_size_mb = preview_path.stat().st_size / 1024 / 1024
    return _encode_video_as_data_url(preview_path), "data_url", preview_size_mb


def _resolve_chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/api/v3"):
        return f"{normalized}/chat/completions"
    return normalized


def _diagnostics_dir(config: dict[str, Any]) -> Path | None:
    runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    value = runtime.get("reports_dir")
    return Path(str(value)) if value else None


def _apply_response_format(
    payload: dict[str, Any],
    *,
    structured_output_mode: str,
    max_final_segments: int,
    max_title_chars: int,
) -> None:
    mode = structured_output_mode.strip().lower()
    if mode == "json_schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": compact_edit_plan_json_schema(
                max_final_segments=max_final_segments,
                max_title_chars=max_title_chars,
            ),
        }
    elif mode == "json_object":
        payload["response_format"] = {"type": "json_object"}
    elif mode in {"none", "disabled", ""}:
        return
    else:
        raise SkillError(f"不支持的 llm.structured_output_mode：{structured_output_mode}")


def _set_repair_prompt(
    payload: dict[str, Any],
    *,
    video_info: dict[str, Any],
    instruction: str,
    target_duration: float,
    duration_policy: dict[str, Any] | None,
    max_final_segments: int,
    max_title_chars: int,
) -> None:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                item["text"] = build_strict_json_edit_prompt(
                    video_info,
                    instruction,
                    target_duration,
                    duration_policy=duration_policy,
                    max_final_segments=max_final_segments,
                    max_title_chars=max_title_chars,
                    repair_mode=True,
                )
                return


def _post_and_diagnose(
    *,
    request_url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout: float,
    attempt_index: int,
    structured_output_mode: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    request_started_at = _utc_now()
    started_monotonic = time.monotonic()
    raw_response = _post_json(request_url, api_key, payload, timeout)
    request_finished_at = _utc_now()
    latency_seconds = round(time.monotonic() - started_monotonic, 3)
    content = _extract_message_content(raw_response)
    usage = raw_response.get("usage") if isinstance(raw_response.get("usage"), dict) else {}
    finish_reason = _extract_finish_reason(raw_response)
    return raw_response, {
        "attempt_index": attempt_index,
        "request_started_at": request_started_at,
        "request_finished_at": request_finished_at,
        "latency_seconds": latency_seconds,
        "finish_reason": finish_reason,
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
        "response_char_count": len(content),
        "response_text": content,
        "structured_output_mode": structured_output_mode,
    }


def _extract_finish_reason(response: dict[str, Any]) -> str | None:
    try:
        finish_reason = response["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return str(finish_reason) if finish_reason is not None else None


def _looks_like_structured_output_unsupported(message: str) -> bool:
    lowered = message.lower()
    return "response_format" in lowered or "json_schema" in lowered or "unsupported" in lowered


def _truncated_suspected(diagnostics: dict[str, Any], content: str) -> bool:
    if diagnostics.get("finish_reason") == "length":
        return True
    tail = content.rstrip()[-20:]
    return bool(content.strip()) and not tail.endswith(("}", "}]"))


def _sum_number(values: list[Any]) -> int | float | None:
    numbers = [value for value in values if isinstance(value, (int, float))]
    return sum(numbers) if numbers else None


def _sum_usage(usages: list[Any]) -> dict[str, Any]:
    fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    result: dict[str, Any] = {}
    for field in fields:
        result[field] = _sum_number([
            usage.get(field)
            for usage in usages
            if isinstance(usage, dict)
        ])
    return result


def _public_attempt_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    return {
        "attempt_index": diagnostics.get("attempt_index"),
        "request_started_at": diagnostics.get("request_started_at"),
        "request_finished_at": diagnostics.get("request_finished_at"),
        "latency_seconds": diagnostics.get("latency_seconds"),
        "finish_reason": diagnostics.get("finish_reason"),
        "usage": diagnostics.get("usage"),
        "prompt_tokens": (diagnostics.get("usage") or {}).get("prompt_tokens"),
        "completion_tokens": (diagnostics.get("usage") or {}).get("completion_tokens"),
        "total_tokens": (diagnostics.get("usage") or {}).get("total_tokens"),
        "response_char_count": diagnostics.get("response_char_count"),
        "json_parse_success": diagnostics.get("json_parse_success"),
        "json_parse_error": diagnostics.get("json_parse_error"),
        "truncated_suspected": diagnostics.get("truncated_suspected"),
        "structured_output_mode": diagnostics.get("structured_output_mode"),
    }


def _write_ark_response_diagnostics(
    diagnostics_dir: Path | None,
    attempts: list[dict[str, Any]],
    response_text: str,
) -> None:
    if diagnostics_dir is None:
        return
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "diagnostics_schema_version": "ark_response_diagnostics_v1",
        "attempts": attempts,
        "last_response_tail": response_text[-1000:],
    }
    (diagnostics_dir / "ark_response_diagnostics.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _post_json(base_url: str, api_key: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base_url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise SkillError(f"Ark LLM HTTP 错误：{exc.code} {details}") from exc
    except urllib.error.URLError as exc:
        raise SkillError(f"Ark LLM 网络错误：{exc.reason}") from exc
    except TimeoutError as exc:
        raise SkillError("Ark LLM 请求超时") from exc
    except OSError as exc:
        raise SkillError(f"Ark LLM 网络错误：{exc}") from exc

    try:
        return json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise SkillError(f"Ark LLM 响应不是合法 JSON：{response_body[:500]}") from exc


def _extract_message_content(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SkillError("Ark LLM 响应缺少 choices[0].message.content") from exc

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
        if text_parts:
            return "\n".join(text_parts)
    raise SkillError("Ark LLM message.content 格式无法解析")


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise SkillError(f"模型输出不是合法 JSON：{cleaned[:500]}") from exc
    if not isinstance(data, dict):
        raise SkillError("模型输出必须是 JSON object")
    return data
