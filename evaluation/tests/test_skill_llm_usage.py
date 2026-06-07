from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "clawcut-video-highlight" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from llm_client import ArkLLMClient  # noqa: E402


def test_ark_usage_is_preserved_in_llm_metadata(tmp_path, monkeypatch):
    preview = tmp_path / "preview.mp4"
    preview.write_bytes(b"preview")
    monkeypatch.setenv("ARK_API_KEY", "test-key")

    def fake_post_json(url, api_key, payload, timeout):
        assert url == "https://ark.example.com/api/v3/chat/completions"
        assert api_key == "test-key"
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "video_type": "test",
                                "video_type_reason": "测试视频。",
                                "highlight_definition": {
                                    "must_keep": ["测试高光"],
                                    "avoid": ["重复内容"],
                                },
                                "final_segments": [{"start": 0, "end": 3, "title": "测试片段"}],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
            },
        }

    monkeypatch.setattr("llm_client._post_json", fake_post_json)
    client = ArkLLMClient()
    plan = client._generate_with_ark(
        preview_video_path=str(preview),
        instruction="帮我剪辑一下这个视频",
        target_duration=30.0,
        video_info={"duration": 30.0},
        config={
            "llm": {
                "model": "ep-test",
                "base_url": "https://ark.example.com/api/v3",
                "video_url": "https://example.com/demo.mp4",
            },
            "duration_policy": {},
        },
    )

    metadata = plan["llm_metadata"]
    assert metadata["backend"] == "ark"
    assert metadata["model"] == "ep-test"
    assert metadata["video_source"] == "url"
    assert metadata["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }
    assert metadata["request_started_at"]
    assert metadata["request_finished_at"]
    assert isinstance(metadata["latency_seconds"], float)


def test_ark_parse_failure_writes_diagnostics(tmp_path, monkeypatch):
    preview = tmp_path / "preview.mp4"
    preview.write_bytes(b"preview")
    reports = tmp_path / "reports"
    monkeypatch.setenv("ARK_API_KEY", "test-key")

    def fake_post_json(url, api_key, payload, timeout):
        return {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": '{"video_type": "test"'},
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }

    monkeypatch.setattr("llm_client._post_json", fake_post_json)
    try:
        ArkLLMClient()._generate_with_ark(
            preview_video_path=str(preview),
            instruction="帮我剪辑一下这个视频",
            target_duration=30.0,
            video_info={"duration": 30.0},
            config={
                "llm": {
                    "model": "ep-test",
                    "base_url": "https://ark.example.com/api/v3",
                    "video_url": "https://example.com/demo.mp4",
                    "invalid_json_retry_count": 0,
                },
                "duration_policy": {},
                "runtime": {"reports_dir": str(reports)},
            },
        )
    except Exception:
        pass
    else:
        raise AssertionError("invalid JSON should fail")

    diagnostics = json.loads((reports / "ark_response_diagnostics.json").read_text(encoding="utf-8"))
    attempt = diagnostics["attempts"][0]
    assert attempt["finish_reason"] == "length"
    assert attempt["total_tokens"] == 18
    assert attempt["json_parse_success"] is False
    assert attempt["truncated_suspected"] is True


def test_structured_output_unsupported_falls_back_once(tmp_path, monkeypatch):
    preview = tmp_path / "preview.mp4"
    preview.write_bytes(b"preview")
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    calls = []

    from utils import SkillError

    def fake_post_json(url, api_key, payload, timeout):
        calls.append(payload)
        if "response_format" in payload:
            raise SkillError("response_format json_schema unsupported")
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "video_type": "test",
                                "video_type_reason": "测试视频。",
                                "highlight_definition": {"must_keep": ["高光"], "avoid": ["重复"]},
                                "final_segments": [{"start": 0, "end": 3, "title": "测试片段"}],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ],
            "usage": {"total_tokens": 10},
        }

    monkeypatch.setattr("llm_client._post_json", fake_post_json)
    plan = ArkLLMClient()._generate_with_ark(
        preview_video_path=str(preview),
        instruction="帮我剪辑一下这个视频",
        target_duration=30.0,
        video_info={"duration": 30.0},
        config={
            "llm": {
                "model": "ep-test",
                "base_url": "https://ark.example.com/api/v3",
                "video_url": "https://example.com/demo.mp4",
                "structured_output_enabled": True,
                "structured_output_mode": "json_schema",
            },
            "duration_policy": {},
        },
    )
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]
    assert plan["llm_metadata"]["structured_output_fallback_used"] is True
