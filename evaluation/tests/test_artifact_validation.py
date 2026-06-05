from __future__ import annotations

import json
from pathlib import Path

from evaluation.artifact_validation import validate_skill_artifacts


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _artifact_tree(
    tmp_path: Path,
    *,
    instruction: str = "剪出高光",
    backend: str = "ark",
    fallback: bool = False,
    input_video: str = "data/input/demo.MP4",
    input_video_sha256: str | None = None,
    final_edit_source_sha256: str | None = None,
) -> Path:
    root = tmp_path / "outputs" / "demo"
    result_summary = {
        "status": "success",
        "input_video": input_video,
        "instruction": instruction,
        "target_duration": None,
        "skill_backend_requested": "ark",
        "skill_backend_used": backend,
        "fallback_used": fallback,
        "source_video_duration": 30,
        "segments_json": str(root / "reports" / "segments.json"),
    }
    if input_video_sha256 is not None:
        result_summary["input_video_sha256"] = input_video_sha256
    if final_edit_source_sha256 is not None:
        result_summary["final_edit_source_sha256"] = final_edit_source_sha256
    _write_json(root / "reports" / "result_summary.json", result_summary)
    _write_json(root / "reports" / "segments.json", {"final_segments": [{"start": 0, "end": 5}]})
    (root / "videos").mkdir(parents=True, exist_ok=True)
    (root / "videos" / "highlight.mp4").write_bytes(b"fake")
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "run.log").write_text("使用的 LLM backend：ark\n", encoding="utf-8")
    return root


def test_artifact_validation_passes_and_reports_backend(tmp_path):
    root = _artifact_tree(tmp_path)
    result = validate_skill_artifacts(
        input_video=Path("data/input/demo.MP4"),
        instruction="剪出高光",
        target_duration=None,
        skill_output_dir=root,
    )
    assert result["artifact_validation_passed"] is True
    assert result["skill_backend_used"] == "ark"
    assert result["fallback_used"] is False
    assert result["input_video_match_method"] == "path"


def test_artifact_validation_accepts_container_path_mapping(tmp_path):
    host_workspace = tmp_path / "host_workspace"
    input_video = host_workspace / "data" / "input" / "demo.MP4"
    input_video.parent.mkdir(parents=True)
    input_video.write_bytes(b"video")
    root = _artifact_tree(
        tmp_path,
        input_video="/home/node/.openclaw/workspace/data/input/demo.MP4",
    )
    result_summary = json.loads((root / "reports" / "result_summary.json").read_text(encoding="utf-8"))
    result_summary["segments_json"] = "/home/node/.openclaw/workspace/outputs/demo/reports/segments.json"
    host_output = host_workspace / "outputs" / "demo"
    (root / "reports" / "result_summary.json").write_text(
        json.dumps(result_summary, ensure_ascii=False),
        encoding="utf-8",
    )
    host_output.parent.mkdir(parents=True)
    root.rename(host_output)

    result = validate_skill_artifacts(
        input_video=input_video,
        instruction="剪出高光",
        target_duration=None,
        skill_output_dir=host_output,
        path_map={"/home/node/.openclaw/workspace": str(host_workspace)},
    )

    assert result["artifact_validation_passed"] is True
    assert result["input_video_match"] is True
    assert result["segments_json_match"] is True


def test_artifact_validation_sha256_match_passes_with_warning(tmp_path):
    source = tmp_path / "copy_a" / "demo.MP4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"same video")
    copied = tmp_path / "copy_b" / "demo.MP4"
    copied.parent.mkdir(parents=True)
    copied.write_bytes(b"same video")

    import hashlib

    digest = hashlib.sha256(b"same video").hexdigest()
    root = _artifact_tree(
        tmp_path,
        input_video=str(source),
        input_video_sha256=digest,
    )
    result = validate_skill_artifacts(
        input_video=copied,
        instruction="剪出高光",
        target_duration=None,
        skill_output_dir=root,
    )
    assert result["artifact_validation_passed"] is True
    assert result["input_video_match"] is True
    assert result["input_video_match_method"] == "sha256"
    assert any("SHA-256" in warning for warning in result["artifact_validation_warnings"])


def test_artifact_validation_sha256_mismatch_fails(tmp_path):
    source = tmp_path / "copy_a" / "demo.MP4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"old video")
    copied = tmp_path / "copy_b" / "demo.MP4"
    copied.parent.mkdir(parents=True)
    copied.write_bytes(b"new video")

    import hashlib

    root = _artifact_tree(
        tmp_path,
        input_video=str(source),
        input_video_sha256=hashlib.sha256(b"old video").hexdigest(),
    )
    result = validate_skill_artifacts(
        input_video=copied,
        instruction="剪出高光",
        target_duration=None,
        skill_output_dir=root,
    )
    assert result["artifact_validation_passed"] is False
    assert result["input_video_match_method"] == "mismatch"


def test_artifact_validation_legacy_without_hash_uses_path_only(tmp_path):
    source = tmp_path / "copy_a" / "demo.MP4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"same video")
    copied = tmp_path / "copy_b" / "demo.MP4"
    copied.parent.mkdir(parents=True)
    copied.write_bytes(b"same video")

    root = _artifact_tree(tmp_path, input_video=str(source))
    result = validate_skill_artifacts(
        input_video=copied,
        instruction="剪出高光",
        target_duration=None,
        skill_output_dir=root,
    )
    assert result["artifact_validation_passed"] is False
    assert result["input_video_match_method"] == "legacy_path"


def test_artifact_validation_detects_instruction_mismatch(tmp_path):
    root = _artifact_tree(tmp_path, instruction="旧指令")
    result = validate_skill_artifacts(
        input_video=Path("data/input/demo.MP4"),
        instruction="新指令",
        target_duration=None,
        skill_output_dir=root,
    )
    assert result["artifact_validation_passed"] is False
    assert any("instruction" in error for error in result["artifact_validation_errors"])


def test_artifact_validation_detects_missing_highlight(tmp_path):
    root = _artifact_tree(tmp_path)
    (root / "videos" / "highlight.mp4").unlink()
    result = validate_skill_artifacts(
        input_video=Path("data/input/demo.MP4"),
        instruction="剪出高光",
        target_duration=None,
        skill_output_dir=root,
    )
    assert result["highlight_video_exists"] is False
    assert result["artifact_validation_passed"] is False


def test_artifact_validation_surfaces_mock_fallback(tmp_path):
    root = _artifact_tree(tmp_path, backend="mock", fallback=True)
    result = validate_skill_artifacts(
        input_video=Path("data/input/demo.MP4"),
        instruction="剪出高光",
        target_duration=None,
        skill_output_dir=root,
    )
    assert result["artifact_validation_passed"] is True
    assert result["skill_backend_used"] == "mock"
    assert result["fallback_used"] is True
