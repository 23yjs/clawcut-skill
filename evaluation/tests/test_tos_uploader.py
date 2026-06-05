from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from evaluation.tos_uploader import (
    TosUploadConfig,
    build_tos_object_key,
    build_tos_upload_config,
    upload_judge_video_to_tos,
)


def test_tos_object_key_uses_instruction_fingerprint():
    key_a = build_tos_object_key(
        key_prefix="output",
        video_id="demo",
        instruction="剪出高光",
        target_duration=None,
        run_id="run1",
    )
    key_b = build_tos_object_key(
        key_prefix="output",
        video_id="demo",
        instruction="只剪投篮得分",
        target_duration=None,
        run_id="run1",
    )
    assert key_a != key_b
    assert key_a.startswith("output/demo/instruction-")
    assert key_a.endswith("/run1/highlight.mp4")


def test_tos_object_key_can_use_eval_case_and_skill_run_layout():
    key = build_tos_object_key(
        key_prefix="judge-videos",
        video_id="demo",
        instruction="剪出高光",
        target_duration=None,
        run_id="legacy",
        eval_run_id="official_v1",
        case_id="generic__demo",
        skill_run_id="run_01",
    )
    assert key == "judge-videos/official_v1/generic__demo/run_01/highlight.mp4"


def test_tos_upload_missing_credentials_returns_structured_failure(tmp_path, monkeypatch):
    video = tmp_path / "highlight.mp4"
    video.write_bytes(b"video")
    monkeypatch.delenv("TOS_ACCESS_KEY", raising=False)
    monkeypatch.delenv("TOS_SECRET_KEY", raising=False)
    record, signed_url = upload_judge_video_to_tos(
        video_path=video,
        video_id="demo",
        instruction="剪出高光",
        target_duration=None,
        run_id="run1",
        config=TosUploadConfig(enabled=True, bucket="clawcut"),
    )
    assert signed_url is None
    assert record["upload_status"] == "failed"
    assert record["error_type"] == "MissingTosCredentials"


def test_tos_upload_success_sanitizes_signed_url(tmp_path, monkeypatch):
    video = tmp_path / "highlight.mp4"
    video.write_bytes(b"video")
    calls: dict[str, object] = {}

    class FakeClient:
        def __init__(self, ak, sk, endpoint, region):
            calls["client"] = (ak, sk, endpoint, region)

        def put_object_from_file(self, bucket, key, file_path):
            calls["put"] = (bucket, key, file_path)

        def pre_signed_url(self, method, *, bucket, key, expires):
            calls["presign"] = (method, bucket, key, expires)
            return SimpleNamespace(
                signed_url=f"https://clawcut.tos-cn-beijing.volces.com/{key}?X-Tos-Signature=secret"
            )

    fake_tos = SimpleNamespace(
        TosClientV2=FakeClient,
        HttpMethodType=SimpleNamespace(Http_Method_Get="GET"),
    )
    monkeypatch.setitem(sys.modules, "tos", fake_tos)
    monkeypatch.setenv("TOS_ACCESS_KEY", "ak")
    monkeypatch.setenv("TOS_SECRET_KEY", "sk")

    record, signed_url = upload_judge_video_to_tos(
        video_path=video,
        video_id="demo",
        instruction="剪出高光",
        target_duration=None,
        run_id="run1",
        config=build_tos_upload_config(enabled=True, bucket="clawcut"),
    )
    assert signed_url and "X-Tos-Signature=secret" in signed_url
    assert record["upload_status"] == "success"
    assert record["object_key"].startswith("output/demo/instruction-")
    assert record["judge_video_url_sanitized"] == f"https://clawcut.tos-cn-beijing.volces.com/{record['object_key']}"
    assert "X-Tos-Signature" not in str(record)
    assert calls["put"][0] == "clawcut"
