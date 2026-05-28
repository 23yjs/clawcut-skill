from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from llm_prompts import SYSTEM_PROMPT, build_strict_json_edit_prompt
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
        llm_config = config.get("llm", {})
        model = str(llm_config.get("model", "") or "").strip()
        base_url = str(llm_config.get("base_url", "") or "").strip()
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
        prompt = build_strict_json_edit_prompt(
            video_info,
            instruction,
            target_duration,
            duration_policy=config.get("duration_policy"),
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
        timeout = float(llm_config.get("timeout_seconds", 120))
        self.logger.info(
            "正在调用 Ark LLM：model=%s url=%s video_source=%s preview_size=%.2fMB",
            model,
            request_url,
            video_source,
            preview_size_mb,
        )
        raw_response = _post_json(request_url, api_key, payload, timeout)
        content = _extract_message_content(raw_response)
        plan = _parse_json_object(content)
        plan.setdefault("llm_metadata", {})
        plan["llm_metadata"].update(
            {
                "backend": "ark",
                "model": model,
                "preview_video_path": preview_video_path,
                "video_source": video_source,
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
