from __future__ import annotations

import json
import socket
import urllib.error

import pytest

from evaluation.ark_resolver_client import (
    ArkResolverConfig,
    ArkResolverError,
    call_ark_resolver,
    parse_resolver_json,
)


GT = {
    "video_id": "demo",
    "video_type": "ecommerce_product",
    "video_summary": "测试视频",
    "semantic_segments": [{"segment_id": "seg_001", "start": 0, "end": 9, "description": "商品外观"}],
}


def _resolver_result() -> dict:
    return {
        "instruction_mode": "specific",
        "selection_scope": "preferential",
        "resolution_status": "resolved",
        "use_default_highlights": False,
        "relevant_segment_ids": ["seg_001"],
        "forbidden_segment_ids": [],
        "unresolved_requirements": [],
        "resolver_reason": "命中商品外观。",
    }


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def _chat_payload(content: str) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def test_normal_ark_json_response(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "secret-key")

    def fake_urlopen(request, timeout):
        return FakeResponse(_chat_payload(json.dumps(_resolver_result(), ensure_ascii=False)))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result, metadata = call_ark_resolver(
        instruction="剪商品外观",
        target_duration=None,
        gt_annotation=GT,
        config=ArkResolverConfig(),
    )
    assert result["relevant_segment_ids"] == ["seg_001"]
    assert metadata["resolver_http_status"] == 200
    assert metadata["resolver_usage"]["total_tokens"] == 15


def test_parse_json_code_block():
    text = "```json\n" + json.dumps(_resolver_result(), ensure_ascii=False) + "\n```"
    result = parse_resolver_json(text)
    assert result["instruction_mode"] == "specific"


def test_http_401_does_not_retry(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "secret-key")
    attempts = {"count": 0}

    def fake_urlopen(request, timeout):
        attempts["count"] += 1
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", None, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ArkResolverError):
        call_ark_resolver(instruction="x", target_duration=None, gt_annotation=GT, config=ArkResolverConfig())
    assert attempts["count"] == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_http_status_retries_once(monkeypatch, status):
    monkeypatch.setenv("ARK_API_KEY", "secret-key")
    attempts = {"count": 0}

    def fake_urlopen(request, timeout):
        attempts["count"] += 1
        raise urllib.error.HTTPError(request.full_url, status, "Retry", None, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ArkResolverError):
        call_ark_resolver(
            instruction="x",
            target_duration=None,
            gt_annotation=GT,
            config=ArkResolverConfig(max_retries=1),
        )
    assert attempts["count"] == 2


def test_timeout_retries_once(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "secret-key")
    attempts = {"count": 0}

    def fake_urlopen(request, timeout):
        attempts["count"] += 1
        raise socket.timeout("slow")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ArkResolverError):
        call_ark_resolver(
            instruction="x",
            target_duration=None,
            gt_annotation=GT,
            config=ArkResolverConfig(max_retries=1),
        )
    assert attempts["count"] == 2


def test_missing_api_key_errors(monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    with pytest.raises(ArkResolverError):
        call_ark_resolver(instruction="x", target_duration=None, gt_annotation=GT, config=ArkResolverConfig())


def test_error_message_does_not_contain_api_key(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "very-secret-key")

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", None, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ArkResolverError) as exc:
        call_ark_resolver(instruction="x", target_duration=None, gt_annotation=GT, config=ArkResolverConfig())
    assert "very-secret-key" not in str(exc.value)
