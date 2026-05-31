from __future__ import annotations

import json
from pathlib import Path

from evaluation.artifact_validation import validate_skill_artifacts


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _artifact_tree(tmp_path: Path, *, instruction: str = "剪出高光", backend: str = "ark", fallback: bool = False) -> Path:
    root = tmp_path / "outputs" / "demo"
    _write_json(
        root / "reports" / "result_summary.json",
        {
            "status": "success",
            "input_video": "data/input/demo.MP4",
            "instruction": instruction,
            "target_duration": None,
            "skill_backend_requested": "ark",
            "skill_backend_used": backend,
            "fallback_used": fallback,
            "source_video_duration": 30,
            "segments_json": str(root / "reports" / "segments.json"),
        },
    )
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
