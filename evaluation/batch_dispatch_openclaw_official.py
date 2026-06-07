from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from .batch_dispatch_openclaw_baseline import (
        ArtifactSearchResult,
        BatchDispatchError,
        collect_existing_records,
        existing_run_dirs,
        find_attempt_artifacts,
        openclaw_meta,
        parse_openclaw_stdout,
        read_json_if_present,
        read_manifest,
    )
except ImportError:  # pragma: no cover - script mode
    from batch_dispatch_openclaw_baseline import (
        ArtifactSearchResult,
        BatchDispatchError,
        collect_existing_records,
        existing_run_dirs,
        find_attempt_artifacts,
        openclaw_meta,
        parse_openclaw_stdout,
        read_json_if_present,
        read_manifest,
    )


SKILL_NAME = "clawcut-video-highlight"
PROTOCOL_NAME = "CLAWCUT_OFFICIAL_COLLECTION_V1"
DEFAULT_CONTAINER_WORKSPACE = "/home/node/.openclaw/workspace"
DEFAULT_MAC_WORKSPACE = "/Users/df/DockerData/openclaw/workspace"
DEFAULT_SKILL_CONFIG = Path("/home/node/.openclaw/workspace/skills/clawcut-video-highlight/config/default.yaml")
RESULT_FIELDS = [
    "case_id",
    "video_id",
    "test_type",
    "priority",
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
    "skill_llm_model",
    "skill_llm_prompt_tokens",
    "skill_llm_completion_tokens",
    "skill_llm_total_tokens",
    "skill_llm_latency_seconds",
    "skill_llm_video_source",
    "skill_llm_request_started_at",
    "skill_llm_request_finished_at",
    "skill_llm_attempt_count",
    "video_editing_elapsed_seconds",
    "skill_run_elapsed_seconds",
    "preview_generation_seconds",
    "ffmpeg_render_seconds",
    "fallback_to_mock_effective",
    "result_summary",
    "highlight_video",
    "run_log",
    "error_message",
    "started_at",
    "finished_at",
]


@dataclass(frozen=True)
class OfficialCase:
    case_id: str
    video_id: str
    video_filename: str
    input_video: str
    skill_output_dir: str
    instruction: str
    target_duration: int | float | None
    llm_video_url: str
    test_type: str
    priority: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OfficialCase":
        required = [
            "case_id",
            "video_id",
            "video_filename",
            "input_video",
            "skill_output_dir",
            "instruction",
            "target_duration",
            "llm_video_url",
            "test_type",
            "priority",
        ]
        missing = [key for key in required if key not in payload]
        if missing:
            raise BatchDispatchError(f"case missing required fields: {', '.join(missing)}")
        return cls(
            case_id=str(payload["case_id"]),
            video_id=str(payload["video_id"]),
            video_filename=str(payload["video_filename"]),
            input_video=str(payload["input_video"]),
            skill_output_dir=str(payload["skill_output_dir"]),
            instruction=str(payload["instruction"]),
            target_duration=payload.get("target_duration"),
            llm_video_url=str(payload["llm_video_url"]),
            test_type=str(payload["test_type"]),
            priority=str(payload["priority"]),
        )


@dataclass(frozen=True)
class AttemptSelection:
    run_id: str | None
    skipped: bool
    reason: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_path_maps(values: list[str] | None) -> dict[str, str]:
    path_map: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise BatchDispatchError(f"--path-map must use FROM=TO format: {value}")
        source, target = value.split("=", 1)
        source = source.strip().rstrip("/")
        target = target.strip().rstrip("/")
        if not source or not target:
            raise BatchDispatchError(f"--path-map cannot be empty: {value}")
        path_map[source] = target
    return path_map


def apply_default_path_map(
    path_map: dict[str, str],
    *,
    mac_workspace: Path = Path(DEFAULT_MAC_WORKSPACE),
) -> dict[str, str]:
    effective = dict(path_map)
    if DEFAULT_CONTAINER_WORKSPACE not in effective and mac_workspace.exists():
        effective[DEFAULT_CONTAINER_WORKSPACE] = str(mac_workspace)
    return effective


def map_path(value: str | Path, path_map: dict[str, str] | None = None) -> Path:
    text = str(value)
    for source_prefix, target_prefix in (path_map or {}).items():
        source = source_prefix.rstrip("/")
        target = target_prefix.rstrip("/")
        if text == source:
            return Path(target)
        if text.startswith(source + "/"):
            return Path(target + text[len(source) :])
    return Path(text)


def load_cases(cases_path: Path) -> list[OfficialCase]:
    if not cases_path.exists():
        raise BatchDispatchError(f"cases file not found: {cases_path}")
    cases: list[OfficialCase] = []
    with cases_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise BatchDispatchError(f"invalid JSONL at line {line_number}: {exc}") from exc
            if not isinstance(payload, dict):
                raise BatchDispatchError(f"line {line_number}: case must be object")
            cases.append(OfficialCase.from_dict(payload))
    validate_cases(cases)
    return cases


def validate_cases(cases: list[OfficialCase]) -> None:
    seen: set[str] = set()
    duplicate_ids: list[str] = []
    output_dirs: set[str] = set()
    duplicate_outputs: list[str] = []
    for case in cases:
        if not case.case_id:
            raise BatchDispatchError("case_id must not be empty")
        if case.case_id in seen:
            duplicate_ids.append(case.case_id)
        seen.add(case.case_id)
        if not case.video_id:
            raise BatchDispatchError(f"{case.case_id}: video_id must not be empty")
        if not case.instruction.strip():
            raise BatchDispatchError(f"{case.case_id}: instruction must not be empty")
        if case.target_duration is not None and not isinstance(case.target_duration, (int, float)):
            raise BatchDispatchError(f"{case.case_id}: target_duration must be number or null")
        if case.target_duration is not None and float(case.target_duration) <= 0:
            raise BatchDispatchError(f"{case.case_id}: target_duration must be positive")
        if not case.llm_video_url.startswith(("http://", "https://")):
            raise BatchDispatchError(f"{case.case_id}: llm_video_url must be http(s)")
        if Path(case.input_video).name != case.video_filename:
            raise BatchDispatchError(f"{case.case_id}: video_filename does not match input_video")
        if case.skill_output_dir in output_dirs:
            duplicate_outputs.append(case.skill_output_dir)
        output_dirs.add(case.skill_output_dir)
    if duplicate_ids:
        raise BatchDispatchError(f"duplicate case_id: {', '.join(sorted(set(duplicate_ids)))}")
    if duplicate_outputs:
        raise BatchDispatchError(f"duplicate skill_output_dir: {', '.join(sorted(set(duplicate_outputs)))}")


def collection_root_for_cases(cases: list[OfficialCase]) -> Path:
    if not cases:
        raise BatchDispatchError("no cases loaded")
    run_dir = Path(cases[0].skill_output_dir)
    if run_dir.name.startswith("run_") and len(run_dir.parents) >= 3:
        return run_dir.parents[2]
    raise BatchDispatchError(f"cannot infer collection root from skill_output_dir: {run_dir}")


def case_root(case: OfficialCase) -> Path:
    return Path(case.skill_output_dir).parent


def run_dir_for(case: OfficialCase, run_id: str) -> Path:
    return case_root(case) / run_id


def session_key_for(case_id: str, run_id: str) -> str:
    return f"clawcut-official-{case_id}-{run_id}"


def choose_next_attempt(case: OfficialCase, *, max_attempts: int, resume: bool) -> AttemptSelection:
    runs = existing_run_dirs(case_root(case))
    if resume:
        for run_dir in runs:
            manifest = read_manifest(run_dir)
            if manifest and manifest.get("collection_status") == "official_success":
                return AttemptSelection(None, skipped=True, reason="official_success already exists")
    if len(runs) >= max_attempts:
        return AttemptSelection(None, skipped=True, reason="max attempts reached")
    next_number = max((int(path.name.split("_", 1)[1]) for path in runs), default=0) + 1
    return AttemptSelection(f"run_{next_number:02d}", skipped=False)


def _target_duration_text(case: OfficialCase) -> str:
    return "未指定" if case.target_duration is None else str(case.target_duration)


def _target_duration_requirement(case: OfficialCase) -> str:
    if case.target_duration is None:
        return "未指定目标时长，不得传入 --target_duration。"
    return f"目标时长为 {case.target_duration} 秒，必须传入 --target_duration {case.target_duration}。"


def render_message(case: OfficialCase, run_id: str) -> str:
    user_instruction_original = f"{case.instruction} {case.input_video}"
    output_dir = run_dir_for(case, run_id)
    return f"""/skill {SKILL_NAME}

[{PROTOCOL_NAME}]

case_id: {case.case_id}
run_id: {run_id}

user_instruction_original:
{user_instruction_original}

instruction:
{case.instruction}

input_video:
{case.input_video}

llm_video_url:
{case.llm_video_url}

output_dir:
{output_dir}

llm_backend:
ark

target_duration:
{_target_duration_text(case)}

执行要求:
1. 必须通过 {SKILL_NAME} Skill 执行。
2. 禁止绕过 Skill 直接处理视频。
3. 禁止修改 Skill 代码、Prompt 或配置。
4. instruction 必须原样传递。
5. {_target_duration_requirement(case)}
6. 执行结束后返回真实产物路径，不得自行猜测路径。

[/{PROTOCOL_NAME}]
"""


def build_openclaw_command(*, agent: str, session_key: str, message: str, timeout_seconds: int) -> list[str]:
    return [
        "openclaw",
        "agent",
        "--agent",
        agent,
        "--session-id",
        session_key,
        "--message",
        message,
        "--json",
        "--timeout",
        str(timeout_seconds),
    ]


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
    if artifact_search.status != "ready":
        return artifact_search.status, artifact_search.error_message
    if openclaw_exit_code != 0:
        return "failed", f"openclaw exited with {openclaw_exit_code}"
    if result_summary.get("status") != "success":
        return "failed", "result_summary status is not success"
    if result_summary.get("skill_backend_used") != "ark":
        return "failed", f"skill backend is {result_summary.get('skill_backend_used') or 'unknown'}"
    if _truthy(result_summary.get("fallback_used")):
        return "failed", "skill fallback was used"
    return "official_success", None


def extract_run_log(result_summary: dict[str, Any], run_dir: Path) -> str | None:
    value = result_summary.get("run_log")
    if isinstance(value, str) and value:
        return value
    candidates = sorted(run_dir.rglob("logs/run.log"))
    return str(candidates[0]) if candidates else None


def manifest_from_attempt(
    *,
    case: OfficialCase,
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
    skill_consumption = (
        result_summary.get("skill_consumption")
        if isinstance(result_summary.get("skill_consumption"), dict)
        else {}
    )
    skill_run_elapsed_seconds = skill_consumption.get(
        "skill_run_elapsed_seconds",
        result_summary.get("skill_run_elapsed_seconds"),
    )
    preview_generation_seconds = skill_consumption.get(
        "preview_generation_seconds",
        result_summary.get("preview_generation_seconds"),
    )
    skill_llm_latency_seconds = skill_consumption.get(
        "skill_llm_latency_seconds",
        result_summary.get("skill_llm_latency_seconds"),
    )
    ffmpeg_render_seconds = skill_consumption.get(
        "ffmpeg_render_seconds",
        result_summary.get("ffmpeg_render_seconds"),
    )
    return {
        "case_id": case.case_id,
        "video_id": case.video_id,
        "test_type": case.test_type,
        "priority": case.priority,
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
        "skill_llm_model": result_summary.get("skill_llm_model") or result_summary.get("skill_model"),
        "skill_llm_prompt_tokens": result_summary.get("skill_llm_prompt_tokens"),
        "skill_llm_completion_tokens": result_summary.get("skill_llm_completion_tokens"),
        "skill_llm_total_tokens": result_summary.get("skill_llm_total_tokens"),
        "skill_llm_latency_seconds": skill_llm_latency_seconds,
        "skill_llm_video_source": result_summary.get("skill_llm_video_source"),
        "skill_llm_request_started_at": result_summary.get("skill_llm_request_started_at"),
        "skill_llm_request_finished_at": result_summary.get("skill_llm_request_finished_at"),
        "skill_llm_attempt_count": result_summary.get("skill_llm_attempt_count"),
        "video_editing_elapsed_seconds": skill_run_elapsed_seconds,
        "skill_run_elapsed_seconds": skill_run_elapsed_seconds,
        "preview_generation_seconds": preview_generation_seconds,
        "ffmpeg_render_seconds": ffmpeg_render_seconds,
        "fallback_to_mock_effective": result_summary.get("fallback_to_mock_effective"),
        "effective_llm_config_snapshot": result_summary.get("effective_llm_config_snapshot"),
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


def _mapped_config_path(path: Path, path_map: dict[str, str]) -> Path:
    return map_path(path, path_map)


def config_has_mock_disabled(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    for line in config_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("fallback_to_mock:"):
            return stripped.split(":", 1)[1].strip() == "false"
    return False


def dry_run(
    *,
    cases: list[OfficialCase],
    agent: str,
    timeout_seconds: int,
    path_map: dict[str, str] | None = None,
    skill_config: Path = DEFAULT_SKILL_CONFIG,
    openclaw_checker: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, Any]:
    output_root = map_path(collection_root_for_cases(cases), path_map)
    errors: list[str] = []
    for case in cases:
        if not map_path(case.input_video, path_map).exists():
            errors.append(f"{case.case_id}: input_video not found: {case.input_video}")
        gt_path = map_path(f"/home/node/.openclaw/workspace/data/eval/{case.video_id}.json", path_map)
        if not gt_path.exists():
            errors.append(f"{case.case_id}: GT not found: {gt_path}")
        if not case.llm_video_url.startswith(("http://", "https://")):
            errors.append(f"{case.case_id}: llm_video_url must be http(s)")
        message = render_message(case, "run_01")
        if not message.startswith(f"/skill {SKILL_NAME}"):
            errors.append(f"{case.case_id}: message does not start with /skill {SKILL_NAME}")
        if f"[{PROTOCOL_NAME}]" not in message:
            errors.append(f"{case.case_id}: message missing {PROTOCOL_NAME}")
    config_path = _mapped_config_path(skill_config, path_map or {})
    if not config_has_mock_disabled(config_path):
        errors.append(f"skill config fallback_to_mock is not false: {config_path}")
    if shutil.which("openclaw") is None:
        errors.append("openclaw command not found")
    elif openclaw_checker is not None:
        try:
            completed = openclaw_checker(["openclaw", "agent", "--help"])
            if completed.returncode != 0:
                errors.append("openclaw agent --help failed")
        except Exception as exc:  # pragma: no cover - real CLI wrapper.
            errors.append(f"openclaw agent --help failed: {exc}")
    output_root.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "failed" if errors else "passed",
        "case_count": len(cases),
        "errors": errors,
        "agent": agent,
        "timeout_seconds": timeout_seconds,
        "checked_at": utc_now(),
    }
    (output_root / "dry_run_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# OpenClaw Official Dry Run",
        "",
        f"- status: {report['status']}",
        f"- case_count: {len(cases)}",
        "",
        "## Errors",
        *(f"- {error}" for error in errors),
        "",
    ]
    (output_root / "dry_run_report.md").write_text("\n".join(lines), encoding="utf-8")
    return report


def run_openclaw_attempt(
    *,
    case: OfficialCase,
    agent: str,
    run_id: str,
    timeout_seconds: int,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    run_dir = run_dir_for(case, run_id)
    if run_dir.exists():
        raise BatchDispatchError(f"refusing to overwrite existing run directory: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=False)
    message = render_message(case, run_id)
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
        completed = command_runner(command, text=True, capture_output=True, timeout=timeout_seconds + 60)
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


def dispatch_batch(
    *,
    cases: list[OfficialCase],
    agent: str,
    resume: bool,
    max_attempts: int,
    timeout_seconds: int,
    path_map: dict[str, str] | None = None,
    skill_config: Path = DEFAULT_SKILL_CONFIG,
) -> list[dict[str, Any]]:
    output_root = map_path(collection_root_for_cases(cases), path_map)
    config_path = _mapped_config_path(skill_config, path_map or {})
    if not config_has_mock_disabled(config_path):
        raise BatchDispatchError(f"formal official dispatch requires fallback_to_mock=false: {config_path}")
    records = collect_existing_records(output_root)
    for case in cases:
        selection = choose_next_attempt(case, max_attempts=max_attempts, resume=resume)
        if selection.skipped:
            append_dispatch_log(output_root, f"{case.case_id}: skipped ({selection.reason})")
            continue
        assert selection.run_id is not None
        append_dispatch_log(output_root, f"{case.case_id}: starting {selection.run_id}")
        manifest = run_openclaw_attempt(
            case=case,
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
    parser = argparse.ArgumentParser(description="Dispatch ClawCut official cases through OpenClaw.")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--agent", default="main")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--only-case", default=None)
    parser.add_argument("--only-priority", default=None)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--path-map", action="append")
    parser.add_argument("--skill-config", type=Path, default=DEFAULT_SKILL_CONFIG)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path_map = apply_default_path_map(parse_path_maps(args.path_map))
    cases_path = map_path(args.cases, path_map)
    try:
        cases = load_cases(cases_path)
    except BatchDispatchError as exc:
        if not str(exc).startswith("cases file not found:"):
            raise
        raise BatchDispatchError(
            f"{exc}\n"
            "If you are dispatching real OpenClaw clipping, run inside the container:\n"
            "  docker exec -it openclaw-openclaw-gateway-1 sh\n"
            "  cd /home/node/.openclaw/workspace/clawcut-skill\n"
            "  python3 evaluation/batch_dispatch_openclaw_official.py "
            "--cases /home/node/.openclaw/workspace/data/eval/cases.official.v2.jsonl "
            "--agent main --dry-run\n"
            "If you are only checking from macOS, pass a path map or rely on the default map:\n"
            "  --path-map /home/node/.openclaw/workspace=/Users/df/DockerData/openclaw/workspace"
        ) from exc
    if args.only_case:
        cases = [case for case in cases if case.case_id == args.only_case]
        if not cases:
            raise BatchDispatchError(f"--only-case not found: {args.only_case}")
    if args.only_priority:
        cases = [case for case in cases if case.priority == args.only_priority]
        if not cases:
            raise BatchDispatchError(f"--only-priority not found: {args.only_priority}")
    if args.limit is not None:
        cases = cases[: args.limit]
    if args.max_attempts <= 0:
        raise BatchDispatchError("--max-attempts must be positive")
    if args.dry_run:
        report = dry_run(
            cases=cases,
            agent=args.agent,
            timeout_seconds=args.timeout_seconds,
            path_map=path_map,
            skill_config=args.skill_config,
            openclaw_checker=subprocess.run,
        )
        return 0 if report["status"] == "passed" else 1
    dispatch_batch(
        cases=cases,
        agent=args.agent,
        resume=args.resume,
        max_attempts=args.max_attempts,
        timeout_seconds=args.timeout_seconds,
        path_map=path_map,
        skill_config=args.skill_config,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
