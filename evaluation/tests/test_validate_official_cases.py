from __future__ import annotations

import json
from pathlib import Path

from evaluation.validate_official_cases import build_readiness_report, main, write_readiness_outputs


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _artifact_tree(tmp_path: Path, *, fallback: bool = False) -> tuple[Path, Path]:
    input_video = tmp_path / "input" / "demo.MP4"
    input_video.parent.mkdir(parents=True)
    input_video.write_bytes(b"video")
    output_dir = tmp_path / "outputs" / "demo"
    _write_json(
        output_dir / "reports" / "result_summary.json",
        {
            "status": "success",
            "input_video": str(input_video),
            "instruction": "剪出高光",
            "target_duration": None,
            "skill_backend_requested": "ark",
            "skill_backend_used": "mock" if fallback else "ark",
            "fallback_used": fallback,
            "segments_json": str(output_dir / "reports" / "segments.json"),
        },
    )
    _write_json(output_dir / "reports" / "segments.json", {"final_segments": [{"start": 0, "end": 1}]})
    (output_dir / "videos").mkdir(parents=True)
    (output_dir / "videos" / "highlight.mp4").write_bytes(b"highlight")
    (output_dir / "logs").mkdir(parents=True)
    (output_dir / "logs" / "run.log").write_text("使用的 LLM backend：ark\n", encoding="utf-8")
    return input_video, output_dir


def _case(input_video: Path, output_dir: Path) -> dict:
    return {
        "case_id": "case_demo",
        "video_id": "demo",
        "priority": "baseline",
        "test_type": "baseline_generic",
        "instruction": "剪出高光",
        "target_duration": None,
        "input_video": str(input_video),
        "skill_output_dir": str(output_dir),
        "tested_capability": "默认高光识别",
    }


def test_readiness_ready_when_artifacts_are_valid(tmp_path) -> None:
    input_video, output_dir = _artifact_tree(tmp_path)
    report = build_readiness_report([_case(input_video, output_dir)])
    assert report["status_counts"]["ready"] == 1
    assert report["ready_for_official_eval"] == 1
    assert report["not_ready_count"] == 0
    assert report["rows"][0]["case_index"] == 1


def test_readiness_detects_missing_artifacts(tmp_path) -> None:
    input_video = tmp_path / "input" / "demo.MP4"
    input_video.parent.mkdir(parents=True)
    input_video.write_bytes(b"video")
    report = build_readiness_report([_case(input_video, tmp_path / "missing")])
    assert report["rows"][0]["status"] == "missing_artifacts"
    assert report["not_ready_count"] == 1


def test_readiness_marks_fallback_as_diagnostic(tmp_path) -> None:
    input_video, output_dir = _artifact_tree(tmp_path, fallback=True)
    report = build_readiness_report([_case(input_video, output_dir)])
    assert report["rows"][0]["status"] == "diagnostic_fallback"


def test_validate_official_cases_cli_writes_outputs(tmp_path) -> None:
    input_video, output_dir = _artifact_tree(tmp_path)
    cases = tmp_path / "cases.jsonl"
    cases.write_text(json.dumps(_case(input_video, output_dir), ensure_ascii=False) + "\n", encoding="utf-8")
    output = tmp_path / "readiness"
    assert main(["--cases", str(cases), "--output-dir", str(output), "--require-ready"]) == 0
    assert (output / "official_case_readiness.json").exists()
    assert (output / "official_case_readiness.csv").exists()
    assert (output / "official_case_readiness.md").exists()
    ready_lines = (output / "official_ready_cases.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(ready_lines) == 1
    assert json.loads(ready_lines[0])["tested_capability"] == "默认高光识别"
    assert (output / "official_diagnostic_cases.jsonl").read_text(encoding="utf-8") == ""


def test_validate_official_cases_cli_accepts_path_map(tmp_path) -> None:
    host_workspace = tmp_path / "host_workspace"
    input_video, output_dir = _artifact_tree(host_workspace)
    result_summary_path = output_dir / "reports" / "result_summary.json"
    result_summary = json.loads(result_summary_path.read_text(encoding="utf-8"))
    result_summary["input_video"] = "/home/node/.openclaw/workspace/input/demo.MP4"
    result_summary["segments_json"] = "/home/node/.openclaw/workspace/outputs/demo/reports/segments.json"
    result_summary_path.write_text(json.dumps(result_summary, ensure_ascii=False), encoding="utf-8")

    case = _case(input_video, output_dir)
    case["input_video"] = "/home/node/.openclaw/workspace/input/demo.MP4"
    case["skill_output_dir"] = "/home/node/.openclaw/workspace/outputs/demo"
    cases = tmp_path / "cases.jsonl"
    cases.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    output = tmp_path / "readiness"

    assert main(
        [
            "--cases",
            str(cases),
            "--output-dir",
            str(output),
            "--path-map",
            f"/home/node/.openclaw/workspace={host_workspace}",
            "--require-ready",
        ]
    ) == 0

    readiness = json.loads((output / "official_case_readiness.json").read_text(encoding="utf-8"))
    assert readiness["ready_for_official_eval"] == 1


def test_readiness_exports_only_ready_cases(tmp_path) -> None:
    ready_input, ready_output = _artifact_tree(tmp_path / "ready")
    fallback_input, fallback_output = _artifact_tree(tmp_path / "fallback", fallback=True)
    missing_input = tmp_path / "missing" / "input" / "demo.MP4"
    missing_input.parent.mkdir(parents=True)
    missing_input.write_bytes(b"video")
    cases = [
        _case(ready_input, ready_output),
        {**_case(fallback_input, fallback_output), "case_id": "case_fallback"},
        {**_case(missing_input, tmp_path / "missing" / "outputs"), "case_id": "case_missing"},
    ]
    report = build_readiness_report(cases)
    output = tmp_path / "readiness"
    write_readiness_outputs(report, output, cases)

    ready_cases = [
        json.loads(line)
        for line in (output / "official_ready_cases.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    diagnostic_cases = [
        json.loads(line)
        for line in (output / "official_diagnostic_cases.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [case["case_id"] for case in ready_cases] == ["case_demo"]
    assert [case["case_id"] for case in diagnostic_cases] == ["case_fallback"]
