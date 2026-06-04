from __future__ import annotations

import json
from pathlib import Path

from evaluation.evaluation_system_audit import (
    EXPECTED_OUTPUT_FILES,
    REQUIRED_SOURCE_FILES,
    build_evaluation_system_audit,
    main,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _official_rows() -> list[dict]:
    test_types = [
        *["baseline_generic"] * 31,
        "specific_following",
        "conflict_exclusion",
        "duration_constrained",
        "high_dynamic",
        "long_dense_video",
    ]
    while len(test_types) < 56:
        test_types.append("specific_following")
    return [
        {
            "case_id": f"case_{index:03d}",
            "video_id": f"video_{index:03d}",
            "test_type": test_type,
            "priority": "baseline" if test_type == "baseline_generic" else "priority",
        }
        for index, test_type in enumerate(test_types, start=1)
    ]


def _prepare_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    for relative_path in REQUIRED_SOURCE_FILES.values():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\n", encoding="utf-8")
    _write_jsonl(repo / "data/eval/cases.official.v1.jsonl", _official_rows())
    _write_jsonl(
        repo / "data/eval/abnormal_cases.v1.jsonl",
        [{"case_id": f"abnormal_{index}", "abnormal_type": "missing_video_path"} for index in range(10)],
    )
    _write_jsonl(
        repo / "data/eval/stability_cases.v1.jsonl",
        [{"case_id": f"stability_{index}"} for index in range(8)],
    )
    _write_jsonl(
        repo / "data/eval/high_dynamic_fps_cases.v1.jsonl",
        [{"case_id": f"fps_{index}"} for index in range(4)],
    )
    return repo


def test_audit_marks_evidence_incomplete_when_outputs_missing(tmp_path) -> None:
    repo = _prepare_repo(tmp_path)
    audit = build_evaluation_system_audit(repo)
    assert audit["status"] == "evidence_incomplete"
    assert audit["blocking_errors"] == []
    assert "official_summary" in audit["missing_output_evidence"]


def test_audit_marks_ready_when_sources_and_outputs_exist(tmp_path) -> None:
    repo = _prepare_repo(tmp_path)
    for relative_path in EXPECTED_OUTPUT_FILES.values():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    audit = build_evaluation_system_audit(repo)
    assert audit["status"] == "ready"
    assert audit["missing_output_evidence"] == []


def test_audit_marks_failed_when_source_artifact_missing(tmp_path) -> None:
    repo = _prepare_repo(tmp_path)
    (repo / "evaluation/run_batch_eval.py").unlink()
    audit = build_evaluation_system_audit(repo)
    assert audit["status"] == "failed"
    assert any("missing source artifact" in error for error in audit["blocking_errors"])


def test_audit_cli_writes_outputs_and_can_fail_on_incomplete(tmp_path) -> None:
    repo = _prepare_repo(tmp_path)
    output = tmp_path / "audit"
    assert main(["--repo-root", str(repo), "--output-dir", str(output), "--require-complete"]) == 1
    assert (output / "evaluation_system_audit.json").exists()
    assert (output / "evaluation_system_audit.md").exists()
