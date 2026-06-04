from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


BASELINE_INSTRUCTION = "帮我剪辑一下这个视频"
SKILL_NAME = "clawcut-video-highlight"
PROTOCOL_NAME = "CLAWCUT_BASELINE_COLLECTION_V1"
RESULT_FIELDS = [
    "case_id",
    "video_id",
    "run_id",
    "collection_status",
    "openclaw_exit_code",
    "openclaw_transport",
    "openclaw_fallback_reason",
    "skill_backend_requested",
    "skill_backend_used",
    "fallback_used",
    "skill_instruction_effective",
    "user_instruction_original",
    "model_interpreted_intent",
    "result_summary",
    "highlight_video",
    "run_log",
    "error_message",
    "started_at",
    "finished_at",
]
KNOWN_NAMING_WARNINGS = [
    "knowledgr-share-demo5.json",
    "product_lanuch_demo1.json",
    "product_lanuch_demo2.json",
]


class BatchDispatchError(ValueError):
    pass


@dataclass(frozen=True)
class BaselineCase:
    case_id: str
    video_id: str
    video_filename: str
    instruction: str
    target_duration: int | float | None
    local_input: str
    llm_video_url: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BaselineCase":
        required = [
            "case_id",
            "video_id",
            "video_filename",
            "instruction",
            "target_duration",
            "local_input",
            "llm_video_url",
        ]
        missing = [key for key in required if key not in payload]
        if missing:
            raise BatchDispatchError(f"case missing required fields: {', '.join(missing)}")
        return cls(
            case_id=str(payload["case_id"]),
            video_id=str(payload["video_id"]),
            video_filename=str(payload["video_filename"]),
            instruction=str(payload["instruction"]),
            target_duration=payload.get("target_duration"),
            local_input=str(payload["local_input"]),
            llm_video_url=str(payload["llm_video_url"]),
        )


@dataclass(frozen=True)
class ArtifactSearchResult:
    status: str
    result_summary_path: Path | None
    highlight_video_path: Path | None
    error_message: str | None = None


@dataclass(frozen=True)
class AttemptSelection:
    run_id: str | None
    skipped: bool
    reason: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_cases(cases_path: Path) -> list[BaselineCase]:
    cases: list[BaselineCase] = []
    with cases_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise BatchDispatchError(f"invalid JSONL at line {line_number}: {exc}") from exc
            cases.append(BaselineCase.from_dict(payload))
    validate_cases(cases)
    return cases


def validate_cases(cases: list[BaselineCase]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for case in cases:
        if not case.case_id:
            raise BatchDispatchError("case_id must not be empty")
        if case.case_id in seen:
            duplicates.append(case.case_id)
        seen.add(case.case_id)
        if not case.video_id:
            raise BatchDispatchError(f"{case.case_id}: video_id must not be empty")
        if not case.video_filename:
            raise BatchDispatchError(f"{case.case_id}: video_filename must not be empty")
        if case.instruction != BASELINE_INSTRUCTION:
            raise BatchDispatchError(f"{case.case_id}: baseline instruction was modified")
        if case.target_duration is not None:
            raise BatchDispatchError(f"{case.case_id}: baseline target_duration must be null")
    if duplicates:
        raise BatchDispatchError(f"duplicate case_id: {', '.join(sorted(set(duplicates)))}")


def session_key_for(case_id: str, run_id: str) -> str:
    return f"clawcut-baseline-{case_id}-{run_id}"


def case_root(output_root: Path, case: BaselineCase) -> Path:
    return output_root / case.video_id / case.case_id


def run_dir_for(output_root: Path, case: BaselineCase, run_id: str) -> Path:
    return case_root(output_root, case) / run_id


def _run_number(path: Path) -> int | None:
    match = re.fullmatch(r"run_(\d+)", path.name)
    if not match:
        return None
    return int(match.group(1))


def existing_run_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        [path for path in root.iterdir() if path.is_dir() and _run_number(path) is not None],
        key=lambda path: _run_number(path) or 0,
    )


def read_manifest(run_dir: Path) -> dict[str, Any] | None:
    manifest_path = run_dir / "attempt_manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def choose_next_attempt(
    output_root: Path,
    case: BaselineCase,
    *,
    max_attempts: int,
    resume: bool,
) -> AttemptSelection:
    root = case_root(output_root, case)
    runs = existing_run_dirs(root)
    if resume:
        for run_dir in runs:
            manifest = read_manifest(run_dir)
            if manifest and manifest.get("collection_status") == "official_success":
                return AttemptSelection(None, skipped=True, reason="official_success already exists")
    if len(runs) >= max_attempts:
        return AttemptSelection(None, skipped=True, reason="max attempts reached")
    next_number = (max((_run_number(path) or 0 for path in runs), default=0) + 1)
    return AttemptSelection(f"run_{next_number:02d}", skipped=False)


def render_message(case: BaselineCase, output_root: Path, run_id: str) -> str:
    target_duration = "未指定" if case.target_duration is None else str(case.target_duration)
    user_instruction_original = f"{case.instruction} {case.local_input}"
    output_dir = run_dir_for(output_root, case, run_id)
    return f"""/skill {SKILL_NAME}

[{PROTOCOL_NAME}]

case_id: {case.case_id}
run_id: {run_id}

user_instruction_original:
{user_instruction_original}

instruction:
{case.instruction}

input_video:
{case.local_input}

llm_video_url:
{case.llm_video_url}

output_dir:
{output_dir}

llm_backend:
ark

target_duration:
{target_duration}

执行要求:
1. 必须通过 {SKILL_NAME} Skill 执行。
2. 禁止绕过 Skill 直接处理视频。
3. 禁止修改 Skill 代码、Prompt 或配置。
4. instruction 必须原样传递。
5. 未指定目标时长，不得传入 --target_duration。
6. 执行结束后返回真实产物路径，不得自行猜测路径。

[/{PROTOCOL_NAME}]
"""


def build_openclaw_command(
    *,
    agent: str,
    session_key: str,
    message: str,
    timeout_seconds: int,
) -> list[str]:
    return [
        "openclaw",
        "agent",
        "--agent",
        agent,
        "--session-key",
        session_key,
        "--message",
        message,
        "--json",
        "--timeout",
        str(timeout_seconds),
    ]


def parse_openclaw_stdout(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        payload = json.loads(stripped)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        pass
    for line in reversed(stripped.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def openclaw_meta(stdout_payload: dict[str, Any]) -> dict[str, Any]:
    meta = stdout_payload.get("meta")
    if isinstance(meta, dict):
        return meta
    return {}


def find_attempt_artifacts(run_dir: Path) -> ArtifactSearchResult:
    result_paths = sorted(run_dir.rglob("reports/result_summary.json"))
    highlight_paths = sorted(run_dir.rglob("videos/highlight.mp4"))
    if len(result_paths) > 1:
        return ArtifactSearchResult("ambiguous_output", None, None, "multiple result_summary.json files found")
    if len(highlight_paths) > 1:
        return ArtifactSearchResult("ambiguous_output", None, None, "multiple highlight.mp4 files found")
    if not result_paths:
        return ArtifactSearchResult("failed", None, highlight_paths[0] if highlight_paths else None, "result_summary.json not found")
    if not highlight_paths:
        return ArtifactSearchResult("failed", result_paths[0], None, "highlight.mp4 not found")
    return ArtifactSearchResult("ready", result_paths[0], highlight_paths[0])


def read_json_if_present(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def classify_attempt(
    *,
    openclaw_exit_code: int,
    stdout_payload: dict[str, Any],
    artifact_search: ArtifactSearchResult,
    result_summary: dict[str, Any],
) -> tuple[str, str | None]:
    meta = openclaw_meta(stdout_payload)
    transport = meta.get("transport") or stdout_payload.get("transport")
    fallback_from = meta.get("fallbackFrom") or meta.get("fallback_from")
    fallback_reason = meta.get("fallbackReason") or meta.get("fallback_reason")
    if transport == "embedded" and (fallback_from == "gateway" or fallback_reason == "gateway_timeout"):
        return "diagnostic_openclaw_fallback", str(fallback_reason or fallback_from)
    if artifact_search.status != "ready":
        return artifact_search.status, artifact_search.error_message

    summary_status = result_summary.get("status")
    fallback_used = _truthy(result_summary.get("fallback_used"))
    skill_backend_used = result_summary.get("skill_backend_used")
    if openclaw_exit_code == 0 and summary_status == "success" and fallback_used:
        return "diagnostic_skill_fallback", None
    if (
        openclaw_exit_code == 0
        and transport == "gateway"
        and summary_status == "success"
        and skill_backend_used == "ark"
        and not fallback_used
    ):
        return "official_success", None
    if openclaw_exit_code != 0:
        return "failed", f"openclaw exited with {openclaw_exit_code}"
    if transport != "gateway":
        return "failed", f"openclaw transport is {transport or 'unknown'}"
    return "failed", "result_summary did not meet official success criteria"


def extract_run_log(result_summary: dict[str, Any], run_dir: Path) -> str | None:
    value = result_summary.get("run_log")
    if isinstance(value, str) and value:
        return value
    candidates = sorted(run_dir.rglob("logs/run.log"))
    return str(candidates[0]) if candidates else None


def manifest_from_attempt(
    *,
    case: BaselineCase,
    run_id: str,
    session_key: str,
    started_at: str,
    finished_at: str,
    openclaw_exit_code: int,
    stdout_payload: dict[str, Any],
    run_dir: Path,
    artifact_search: ArtifactSearchResult,
    result_summary: dict[str, Any],
    collection_status: str,
    error_message: str | None,
) -> dict[str, Any]:
    meta = openclaw_meta(stdout_payload)
    return {
        "case_id": case.case_id,
        "video_id": case.video_id,
        "run_id": run_id,
        "session_key": session_key,
        "collection_status": collection_status,
        "openclaw_exit_code": openclaw_exit_code,
        "openclaw_transport": meta.get("transport") or stdout_payload.get("transport"),
        "openclaw_fallback_from": meta.get("fallbackFrom") or meta.get("fallback_from"),
        "openclaw_fallback_reason": meta.get("fallbackReason") or meta.get("fallback_reason"),
        "result_summary": str(artifact_search.result_summary_path) if artifact_search.result_summary_path else None,
        "highlight_video": str(artifact_search.highlight_video_path) if artifact_search.highlight_video_path else None,
        "run_log": extract_run_log(result_summary, run_dir),
        "skill_backend_requested": result_summary.get("skill_backend_requested"),
        "skill_backend_used": result_summary.get("skill_backend_used"),
        "fallback_used": result_summary.get("fallback_used"),
        "skill_instruction_effective": result_summary.get("skill_instruction_effective") or result_summary.get("instruction"),
        "user_instruction_original": result_summary.get("user_instruction_original"),
        "model_interpreted_intent": result_summary.get("model_interpreted_intent"),
        "error_message": error_message,
        "started_at": started_at,
        "finished_at": finished_at,
    }


def row_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {field: manifest.get(field) for field in RESULT_FIELDS}


def write_batch_outputs(output_root: Path, records: list[dict[str, Any]]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "batch_results.jsonl").write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + ("\n" if records else ""),
        encoding="utf-8",
    )
    with (output_root / "batch_results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(row_from_manifest(record))
    counts: dict[str, int] = {}
    for record in records:
        status = str(record.get("collection_status"))
        counts[status] = counts.get(status, 0) + 1
    progress = {
        "updated_at": utc_now(),
        "attempt_count": len(records),
        "status_counts": counts,
        "results_csv": str(output_root / "batch_results.csv"),
        "results_jsonl": str(output_root / "batch_results.jsonl"),
    }
    (output_root / "batch_progress.json").write_text(
        json.dumps(progress, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_dispatch_log(output_root: Path, message: str) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    with (output_root / "batch_dispatch.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{utc_now()} {message}\n")


def collect_existing_records(output_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(output_root.rglob("attempt_manifest.json")):
        payload = read_json_if_present(path)
        if payload:
            records.append(payload)
    return records


def run_openclaw_attempt(
    *,
    case: BaselineCase,
    output_root: Path,
    agent: str,
    run_id: str,
    timeout_seconds: int,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    run_dir = run_dir_for(output_root, case, run_id)
    if run_dir.exists():
        raise BatchDispatchError(f"refusing to overwrite existing run directory: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=False)
    message = render_message(case, output_root, run_id)
    session_key = session_key_for(case.case_id, run_id)
    command = build_openclaw_command(
        agent=agent,
        session_key=session_key,
        message=message,
        timeout_seconds=timeout_seconds,
    )
    started_at = utc_now()
    (run_dir / "dispatch_message.txt").write_text(message, encoding="utf-8")
    openclaw_exit_code = -1
    stdout_text = ""
    stderr_text = ""
    try:
        completed = command_runner(
            command,
            text=True,
            capture_output=True,
            timeout=timeout_seconds + 60,
        )
        openclaw_exit_code = int(completed.returncode)
        stdout_text = completed.stdout or ""
        stderr_text = completed.stderr or ""
    except FileNotFoundError as exc:
        stderr_text = f"OpenClaw command not found: {exc}"
    except subprocess.TimeoutExpired as exc:
        stderr_text = f"OpenClaw command timed out after {timeout_seconds} seconds: {exc}"
    finished_at = utc_now()
    stdout_payload = parse_openclaw_stdout(stdout_text)
    (run_dir / "openclaw_stdout.json").write_text(
        json.dumps(stdout_payload or {"raw_stdout": stdout_text}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "openclaw_stderr.log").write_text(stderr_text, encoding="utf-8")
    artifact_search = find_attempt_artifacts(run_dir)
    result_summary = read_json_if_present(artifact_search.result_summary_path)
    collection_status, error_message = classify_attempt(
        openclaw_exit_code=openclaw_exit_code,
        stdout_payload=stdout_payload,
        artifact_search=artifact_search,
        result_summary=result_summary,
    )
    manifest = manifest_from_attempt(
        case=case,
        run_id=run_id,
        session_key=session_key,
        started_at=started_at,
        finished_at=finished_at,
        openclaw_exit_code=openclaw_exit_code,
        stdout_payload=stdout_payload,
        run_dir=run_dir,
        artifact_search=artifact_search,
        result_summary=result_summary,
        collection_status=collection_status,
        error_message=error_message,
    )
    (run_dir / "attempt_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def dry_run(
    *,
    cases: list[BaselineCase],
    output_root: Path,
    agent: str,
    timeout_seconds: int,
    openclaw_checker: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    for warning in KNOWN_NAMING_WARNINGS:
        warnings.append(f"known naming anomaly retained: {warning}")
    if not any(case.video_id == "cooking_demo2" for case in cases):
        warnings.append("cooking_demo2 GT 尚未提交，未加入正式 baseline 清单")
    for case in cases:
        local_input = Path(case.local_input)
        if not local_input.exists():
            errors.append(f"{case.case_id}: local_input not found: {case.local_input}")
        if not case.llm_video_url:
            errors.append(f"{case.case_id}: llm_video_url is empty")
        if Path(case.local_input).name != case.video_filename:
            errors.append(f"{case.case_id}: video_filename does not match local_input")
        message = render_message(case, output_root, "run_01")
        if not message.startswith(f"/skill {SKILL_NAME}"):
            errors.append(f"{case.case_id}: message does not start with /skill {SKILL_NAME}")
        if f"instruction:\n{BASELINE_INSTRUCTION}" not in message:
            errors.append(f"{case.case_id}: baseline instruction missing from message")
    if shutil.which("openclaw") is None:
        errors.append("openclaw command not found")
    elif openclaw_checker is not None:
        try:
            completed = openclaw_checker(["openclaw", "agent", "--help"])
            if completed.returncode != 0:
                errors.append("openclaw agent --help failed")
        except Exception as exc:  # pragma: no cover - defensive wrapper for real CLI.
            errors.append(f"openclaw agent --help failed: {exc}")

    output_root.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "failed" if errors else "passed",
        "case_count": len(cases),
        "errors": errors,
        "warnings": warnings,
        "agent": agent,
        "timeout_seconds": timeout_seconds,
        "checked_at": utc_now(),
    }
    (output_root / "dry_run_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_lines = [
        "# OpenClaw Baseline Dry Run",
        "",
        f"- status: {report['status']}",
        f"- case_count: {len(cases)}",
        "",
        "## Errors",
        *(f"- {error}" for error in errors),
        "",
        "## Warnings",
        *(f"- {warning}" for warning in warnings),
        "",
    ]
    (output_root / "dry_run_report.md").write_text("\n".join(md_lines), encoding="utf-8")
    return report


def dispatch_batch(
    *,
    cases: list[BaselineCase],
    output_root: Path,
    agent: str,
    resume: bool,
    max_attempts: int,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    records = collect_existing_records(output_root)
    for case in cases:
        selection = choose_next_attempt(output_root, case, max_attempts=max_attempts, resume=resume)
        if selection.skipped:
            append_dispatch_log(output_root, f"{case.case_id}: skipped ({selection.reason})")
            continue
        assert selection.run_id is not None
        append_dispatch_log(output_root, f"{case.case_id}: starting {selection.run_id}")
        manifest = run_openclaw_attempt(
            case=case,
            output_root=output_root,
            agent=agent,
            run_id=selection.run_id,
            timeout_seconds=timeout_seconds,
        )
        records = collect_existing_records(output_root)
        write_batch_outputs(output_root, records)
        append_dispatch_log(output_root, f"{case.case_id}: {manifest['collection_status']}")
    write_batch_outputs(output_root, records)
    return records


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dispatch ClawCut baseline cases through OpenClaw.")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--agent", default="main")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--only-case", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cases = load_cases(args.cases)
    if args.only_case:
        cases = [case for case in cases if case.case_id == args.only_case]
        if not cases:
            raise BatchDispatchError(f"--only-case not found: {args.only_case}")
    if args.max_attempts <= 0:
        raise BatchDispatchError("--max-attempts must be positive")
    if args.dry_run:
        report = dry_run(
            cases=cases,
            output_root=args.output_root,
            agent=args.agent,
            timeout_seconds=args.timeout_seconds,
            openclaw_checker=subprocess.run,
        )
        return 0 if report["status"] == "passed" else 1
    dispatch_batch(
        cases=cases,
        output_root=args.output_root,
        agent=args.agent,
        resume=args.resume,
        max_attempts=args.max_attempts,
        timeout_seconds=args.timeout_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
