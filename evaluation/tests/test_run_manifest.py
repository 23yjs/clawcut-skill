from __future__ import annotations

from pathlib import Path

from evaluation.run_manifest import build_run_manifest, sanitize_url, sha256_text


def test_sanitize_url_drops_query_string():
    assert sanitize_url("https://example.com/video.mp4?secret=1") == "https://example.com/video.mp4"


def test_manifest_hashes_judge_url_but_does_not_store_query(tmp_path):
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"video")
    gt = tmp_path / "demo.json"
    gt.write_text("{}", encoding="utf-8")
    manifest = build_run_manifest(
        run_id="run1",
        repo_root=Path("."),
        input_video_path=video,
        gt_path=gt,
        instruction="剪出高光",
        target_duration=None,
        duration_policy_mode="bounded_auto",
        skill_prompt_version="skill",
        resolver_prompt_version="resolver",
        aesthetic_judge_prompt_version="judge",
        skill_model="skill-model",
        resolver_model="resolver-model",
        aesthetic_judge_model="judge-model",
        skill_backend_requested="ark",
        skill_backend_used="ark",
        fallback_used=False,
        generated_case_path=None,
        segments_json_path=None,
        highlight_video_path=None,
        judge_repeats=1,
        judge_video_url="https://example.com/highlight.mp4?debug=1",
    )
    assert manifest["judge_video_url_sanitized"] == "https://example.com/highlight.mp4"
    assert manifest["judge_video_url_sha256"] == sha256_text("https://example.com/highlight.mp4?debug=1")
    assert "debug=1" not in str(manifest)
