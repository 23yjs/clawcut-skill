from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

try:
    from .resolver_prompts import RESOLVER_PROMPT_VERSION, RESOLVER_SYSTEM_PROMPT, build_resolver_user_content
except ImportError:  # pragma: no cover - script mode
    from resolver_prompts import RESOLVER_PROMPT_VERSION, RESOLVER_SYSTEM_PROMPT, build_resolver_user_content


@dataclass(frozen=True)
class ArkResolverConfig:
    api_key_env: str = "ARK_API_KEY"
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    model: str = "doubao-seed-2-0-lite-260428"
    temperature: float = 0.0
    timeout_seconds: int = 120
    max_retries: int = 1


class ArkResolverError(RuntimeError):
    pass


def parse_resolver_json(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError as exc:
                raise ArkResolverError(f"Ark Resolver JSON 解析失败：{exc}") from exc
        else:
            raise ArkResolverError("Ark Resolver JSON 解析失败：模型返回内容不是合法 JSON")
    if not isinstance(parsed, dict):
        raise ArkResolverError("Ark Resolver JSON 解析失败：根节点必须是 object")
    return parsed


def _metadata(
    *,
    config: ArkResolverConfig,
    latency: float,
    attempts: int,
    http_status: int | None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    usage = usage or {}
    return {
        "resolver_model": config.model,
        "resolver_prompt_version": RESOLVER_PROMPT_VERSION,
        "resolver_latency_seconds": round(latency, 3),
        "resolver_attempt_count": attempts,
        "resolver_http_status": http_status,
        "resolver_usage": {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
    }


def _is_retryable_http_status(status: int) -> bool:
    return status in {429, 500, 502, 503, 504}


def _extract_content(response_payload: dict[str, Any]) -> str:
    try:
        return str(response_payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise ArkResolverError("Ark Resolver 响应缺少 choices[0].message.content") from exc


def call_ark_resolver(
    *,
    instruction: str,
    target_duration: float | None,
    gt_annotation: dict[str, Any],
    config: ArkResolverConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        raise ArkResolverError(f"缺少 Ark Resolver API Key 环境变量：{config.api_key_env}")

    url = f"{config.base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": config.model,
        "temperature": config.temperature,
        "messages": [
            {"role": "system", "content": RESOLVER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_resolver_user_content(
                    instruction=instruction,
                    target_duration=target_duration,
                    gt_annotation=gt_annotation,
                ),
            },
        ],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    max_attempts = max(1, int(config.max_retries) + 1)
    started = time.monotonic()
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
                raw_text = response.read().decode("utf-8")
                response_payload = json.loads(raw_text)
                content = _extract_content(response_payload)
                parsed = parse_resolver_json(content)
                metadata = _metadata(
                    config=config,
                    latency=time.monotonic() - started,
                    attempts=attempt,
                    http_status=getattr(response, "status", 200),
                    usage=response_payload.get("usage"),
                )
                return parsed, metadata
        except urllib.error.HTTPError as exc:
            last_error = exc
            if _is_retryable_http_status(exc.code) and attempt < max_attempts:
                continue
            raise ArkResolverError(f"Ark Resolver HTTP 错误：status={exc.code}") from exc
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            last_error = exc
            if attempt < max_attempts:
                continue
            raise ArkResolverError(f"Ark Resolver 网络或超时错误：{exc.__class__.__name__}") from exc
        except json.JSONDecodeError as exc:
            raise ArkResolverError(f"Ark Resolver HTTP 响应不是合法 JSON：{exc}") from exc

    raise ArkResolverError(f"Ark Resolver 调用失败：{last_error.__class__.__name__ if last_error else 'unknown'}")
