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
                                "user_intent": "测试",
                                "final_segments": [],
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
