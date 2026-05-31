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
    from .aesthetic_judge_prompts import (
        AESTHETIC_JUDGE_PROMPT_VERSION,
        AESTHETIC_JUDGE_SYSTEM_PROMPT,
        build_aesthetic_judge_text_prompt,
    )
except ImportError:  # pragma: no cover - script mode
    from aesthetic_judge_prompts import (
        AESTHETIC_JUDGE_PROMPT_VERSION,
        AESTHETIC_JUDGE_SYSTEM_PROMPT,
        build_aesthetic_judge_text_prompt,
    )


@dataclass(frozen=True)
class ArkAestheticJudgeConfig:
    api_key_env: str = "ARK_API_KEY"
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    model: str = "ep-20260526173832-2vrr2"
    temperature: float = 0.0
    timeout_seconds: int = 120
    max_retries: int = 1


class ArkAestheticJudgeError(RuntimeError):
    pass


def parse_aesthetic_judge_json(text: str) -> dict[str, Any]:
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
            try:
                parsed = json.loads("\n".join(lines).strip())
            except json.JSONDecodeError as exc:
                raise ArkAestheticJudgeError(f"Ark Aesthetic Judge JSON 解析失败：{exc}") from exc
        else:
            raise ArkAestheticJudgeError("Ark Aesthetic Judge JSON 解析失败：模型返回内容不是合法 JSON")
    if not isinstance(parsed, dict):
        raise ArkAestheticJudgeError("Ark Aesthetic Judge JSON 根节点必须是 object")
    return parsed


def _extract_content(response_payload: dict[str, Any]) -> str:
    try:
        return str(response_payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise ArkAestheticJudgeError("Ark Aesthetic Judge 响应缺少 choices[0].message.content") from exc


def _retryable(status: int) -> bool:
    return status in {429, 500, 502, 503, 504}


def _metadata(
    *,
    config: ArkAestheticJudgeConfig,
    latency: float,
    attempts: int,
    http_status: int | None,
    usage: dict[str, Any] | None,
) -> dict[str, Any]:
    usage = usage or {}
    return {
        "aesthetic_judge_model": config.model,
        "aesthetic_judge_prompt_version": AESTHETIC_JUDGE_PROMPT_VERSION,
        "aesthetic_judge_latency_seconds": round(latency, 3),
        "aesthetic_judge_attempt_count": attempts,
        "aesthetic_judge_http_status": http_status,
        "aesthetic_judge_usage": {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
    }


def call_ark_aesthetic_judge(
    *,
    judge_video_url: str,
    instruction: str,
    video_type: str,
    target_duration: float | None,
    rendered_duration: float | None,
    config: ArkAestheticJudgeConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        raise ArkAestheticJudgeError(f"缺少 Ark Aesthetic Judge API Key 环境变量：{config.api_key_env}")
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": config.model,
        "temperature": config.temperature,
        "messages": [
            {"role": "system", "content": AESTHETIC_JUDGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": build_aesthetic_judge_text_prompt(
                            instruction=instruction,
                            video_type=video_type,
                            target_duration=target_duration,
                            rendered_duration=rendered_duration,
                        ),
                    },
                    {"type": "video_url", "video_url": {"url": judge_video_url}},
                ],
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
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
                raw_text = response.read().decode("utf-8")
                response_payload = json.loads(raw_text)
                parsed = parse_aesthetic_judge_json(_extract_content(response_payload))
                return parsed, _metadata(
                    config=config,
                    latency=time.monotonic() - started,
                    attempts=attempt,
                    http_status=getattr(response, "status", 200),
                    usage=response_payload.get("usage"),
                )
        except urllib.error.HTTPError as exc:
            last_error = exc
            if _retryable(exc.code) and attempt < max_attempts:
                continue
            raise ArkAestheticJudgeError(f"Ark Aesthetic Judge HTTP 错误：status={exc.code}") from exc
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            last_error = exc
            if attempt < max_attempts:
                continue
            raise ArkAestheticJudgeError(f"Ark Aesthetic Judge 网络或超时错误：{exc.__class__.__name__}") from exc
        except json.JSONDecodeError as exc:
            raise ArkAestheticJudgeError(f"Ark Aesthetic Judge HTTP 响应不是合法 JSON：{exc}") from exc
    raise ArkAestheticJudgeError(f"Ark Aesthetic Judge 调用失败：{last_error.__class__.__name__ if last_error else 'unknown'}")
