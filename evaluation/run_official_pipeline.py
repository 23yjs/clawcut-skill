from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

try:
    from .artifact_validation import validate_skill_artifacts
    from .run_batch_eval import main_from_args as run_batch_eval_main
    from .tos_uploader import build_tos_object_key
    from .validate_official_cases import build_readiness_report, read_jsonl, write_readiness_outputs
except ImportError:  # pragma: no cover - script mode
    from artifact_validation import validate_skill_artifacts
    from run_batch_eval import main_from_args as run_batch_eval_main
    from tos_uploader import build_tos_object_key
    from validate_official_cases import build_readiness_report, read_jsonl, write_readiness_outputs


READY_STATUSES = {"ready"}
DIAGNOSTIC_STATUSES = {"diagnostic_fallback"}
MISSING_STATUSES = {"missing_artifacts", "invalid_artifacts", "invalid_case", "ambiguous_output"}


def _parse_path_maps(values: list[str] | None) -> dict[str, str]:
    path_map: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(f"--path-map must use FROM=TO format: {value}")
        source, target = value.split("=", 1)
        source = source.strip().rstrip("/")
        target = target.strip().rstrip("/")
        if not source or not target:
            raise ValueError(f"--path-map cannot be empty: {value}")
        path_map[source] = target
    return path_map


def _map_path(value: Any, path_map: dict[str, str]) -> Path:
    text = str(value or "")
    for source_prefix, target_prefix in path_map.items():
        source = source_prefix.rstrip("/")
        target = target_prefix.rstrip("/")
        if text == source:
            return Path(target)
        if text.startswith(source + "/"):
            return Path(target + text[len(source) :])
    return Path(text)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


def _rows_by_status(cases: list[dict[str, Any]], report: dict[str, Any], statuses: set[str]) -> list[dict[str, Any]]:
    indexes = {
        int(row["case_index"])
        for row in report.get("rows", [])
        if row.get("status") in statuses and row.get("case_index")
    }
    return [case for index, case in enumerate(cases, start=1) if index in indexes]


def _attempt_manifest_path(host_skill_output_dir: Path) -> Path:
    return host_skill_output_dir / "attempt_manifest.json"


def _read_evaluation_sidecar(eval_output_dir: Path, case_id: str, filename: str) -> dict[str, Any]:
    return _read_json(eval_output_dir / "runs" / case_id / filename)


def _skill_run_id(case: dict[str, Any]) -> str:
    return str(case.get("skill_run_id") or Path(str(case.get("skill_output_dir") or "")).name or "run_01")


def _artifact_record(
    *,
    case: dict[str, Any],
    readiness_row: dict[str, Any],
    path_map: dict[str, str],
    output_dir: Path,
    eval_run_id: str,
    tos_key_prefix: str,
    effect_eval_dir: Path,
) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "")
    host_input_video = _map_path(case.get("input_video"), path_map)
    host_skill_output_dir = _map_path(case.get("skill_output_dir"), path_map)
    validation: dict[str, Any] = {}
    if case.get("input_video") and case.get("skill_output_dir"):
        validation = validate_skill_artifacts(
            input_video=host_input_video,
            instruction=str(case.get("instruction") or ""),
            target_duration=case.get("target_duration"),
            skill_output_dir=host_skill_output_dir,
            path_map=path_map,
        )
    paths = validation.get("paths") if isinstance(validation.get("paths"), dict) else {}
    attempt_manifest = _read_json(_attempt_manifest_path(host_skill_output_dir))
    tos_upload = _read_evaluation_sidecar(effect_eval_dir, case_id, "tos_upload.json")
    evaluation_result = _read_evaluation_sidecar(effect_eval_dir, case_id, "evaluation_result.json")
    readiness_status = readiness_row.get("status")
    effect_eligibility = {
        "ready": "ready_for_effect_eval",
        "diagnostic_fallback": "diagnostic_skill_fallback",
    }.get(str(readiness_status), readiness_status)
    object_key = tos_upload.get("object_key") or build_tos_object_key(
        key_prefix=tos_key_prefix,
        video_id=str(case.get("video_id") or ""),
        instruction=str(case.get("instruction") or ""),
        target_duration=case.get("target_duration"),
        run_id=case_id or "run",
        eval_run_id=eval_run_id,
        case_id=case_id,
        skill_run_id=_skill_run_id(case),
    )
    return {
        "case_id": case_id,
        "video_id": case.get("video_id"),
        "instruction": case.get("instruction"),
        "container_input_video": case.get("input_video"),
        "host_input_video": str(host_input_video),
        "container_skill_output_dir": case.get("skill_output_dir"),
        "host_skill_output_dir": str(host_skill_output_dir),
        "result_summary": paths.get("result_summary"),
        "segments_json": paths.get("segments_json"),
        "highlight_video": paths.get("highlight_video"),
        "run_log": paths.get("run_log"),
        "artifact_validation_passed": validation.get("artifact_validation_passed"),
        "skill_backend_used": validation.get("skill_backend_used") or attempt_manifest.get("skill_backend_used"),
        "fallback_used": validation.get("fallback_used") if validation else attempt_manifest.get("fallback_used"),
        "openclaw_transport": attempt_manifest.get("openclaw_transport"),
        "collection_status": attempt_manifest.get("collection_status"),
        "readiness_status": readiness_status,
        "effect_eval_eligibility": effect_eligibility,
        "tos_object_key": object_key,
        "tos_upload_status": tos_upload.get("upload_status") or tos_upload.get("status"),
        "judge_video_url_sha256": tos_upload.get("judge_video_url_sha256"),
        "evaluation_status": evaluation_result.get("evaluation_status"),
    }


def write_artifact_manifest(
    *,
    cases: list[dict[str, Any]],
    report: dict[str, Any],
    path_map: dict[str, str],
    output_dir: Path,
    tos_key_prefix: str,
    effect_eval_dir: Path,
) -> list[dict[str, Any]]:
    rows_by_index = {int(row["case_index"]): row for row in report.get("rows", []) if row.get("case_index")}
    records = [
        _artifact_record(
            case=case,
            readiness_row=rows_by_index.get(index, {}),
            path_map=path_map,
            output_dir=output_dir,
            eval_run_id=output_dir.name,
            tos_key_prefix=tos_key_prefix,
            effect_eval_dir=effect_eval_dir,
        )
        for index, case in enumerate(cases, start=1)
    ]
    _write_jsonl(output_dir / "artifact_manifest.jsonl", records)
    return records


def write_manual_upload_todo(path: Path, records: list[dict[str, Any]]) -> None:
    fieldnames = ["case_id", "highlight_video", "tos_object_key", "reason"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            if record.get("effect_eval_eligibility") != "ready_for_effect_eval":
                continue
            if record.get("tos_upload_status") == "success":
                continue
            writer.writerow(
                {
                    "case_id": record.get("case_id"),
                    "highlight_video": record.get("highlight_video"),
                    "tos_object_key": record.get("tos_object_key"),
                    "reason": record.get("tos_upload_status") or "not_uploaded",
                }
            )


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    path_map = _parse_path_maps(args.path_map)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = read_jsonl(args.cases)
    report = build_readiness_report(cases, path_map)
    readiness_dir = output_dir / "readiness"
    write_readiness_outputs(report, readiness_dir, cases)

    ready_cases = _rows_by_status(cases, report, READY_STATUSES)
    missing_cases = _rows_by_status(cases, report, MISSING_STATUSES)
    rerun_cases = _rows_by_status(cases, report, DIAGNOSTIC_STATUSES | MISSING_STATUSES)
    _write_jsonl(output_dir / "official_missing_cases.jsonl", missing_cases)
    _write_jsonl(output_dir / "rerun_openclaw_cases.jsonl", rerun_cases)

    effect_eval_dir = output_dir / "effect_eval"
    if ready_cases:
        batch_args = [
            "--cases",
            str(readiness_dir / "official_ready_cases.jsonl"),
            "--gt_dir",
            str(args.gt_dir),
            "--output_dir",
            str(effect_eval_dir),
        ]
        for path_map_value in args.path_map:
            batch_args.extend(["--path-map", path_map_value])
        if args.judge_url_map:
            batch_args.extend(["--judge-url-map", str(args.judge_url_map)])
        if not args.selection_only:
            batch_args.append("--auto_upload_judge_video")
            for option, value in (
                ("--tos_bucket", args.tos_bucket),
                ("--tos_region", args.tos_region),
                ("--tos_endpoint", args.tos_endpoint),
                ("--tos_key_prefix", args.tos_key_prefix),
                ("--tos_presign_expires_seconds", args.tos_presign_expires_seconds),
            ):
                if value is not None:
                    batch_args.extend([option, str(value)])
        run_batch_eval_main(batch_args)

    records = write_artifact_manifest(
        cases=cases,
        report=report,
        path_map=path_map,
        output_dir=output_dir,
        tos_key_prefix=args.tos_key_prefix or "output",
        effect_eval_dir=effect_eval_dir,
    )
    write_manual_upload_todo(output_dir / "manual_upload_todo.csv", records)
    summary = {
        "case_count": len(cases),
        "ready_for_effect_eval": len(ready_cases),
        "diagnostic_case_count": len(_rows_by_status(cases, report, DIAGNOSTIC_STATUSES)),
        "missing_case_count": len(missing_cases),
        "selection_only": bool(args.selection_only),
        "effect_eval_dir": str(effect_eval_dir),
        "artifact_manifest": str(output_dir / "artifact_manifest.jsonl"),
        "manual_upload_todo": str(output_dir / "manual_upload_todo.csv"),
    }
    (output_dir / "official_pipeline_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local official ClawCut evaluation pipeline.")
    parser.add_argument("--cases", type=Path, default=Path("data/eval/cases.official.v1.jsonl"))
    parser.add_argument("--gt-dir", type=Path, default=Path("data/eval"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--path-map", action="append", required=True)
    parser.add_argument("--selection-only", action="store_true")
    parser.add_argument("--judge-url-map", type=Path)
    parser.add_argument("--tos_bucket", default=None)
    parser.add_argument("--tos_region", default=None)
    parser.add_argument("--tos_endpoint", default=None)
    parser.add_argument("--tos_key_prefix", default=None)
    parser.add_argument("--tos_presign_expires_seconds", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    summary = run_pipeline(parse_args(argv))
    print(f"official pipeline 完成：{summary['effect_eval_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
