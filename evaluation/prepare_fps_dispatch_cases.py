from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_CASES = Path("data/eval/high_dynamic_fps_cases.v2.with_llm_video_url.jsonl")
DEFAULT_OFFICIAL_CASES = Path("data/eval/cases.official.v2.jsonl")
DEFAULT_OUTPUT = Path("data/eval/generated/high_dynamic_fps.dispatch.v2.jsonl")
CONTAINER_WORKSPACE = "/home/node/.openclaw/workspace"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{line_number}: JSONL 行必须是 object")
            rows.append(payload)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


def official_by_video_id(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows = read_jsonl(path)
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        video_id = str(row.get("video_id") or "")
        if video_id and video_id not in mapped:
            mapped[video_id] = row
    return mapped


def _video_filename(case: dict[str, Any], source: dict[str, Any] | None) -> str:
    if source and source.get("video_filename"):
        return str(source["video_filename"])
    value = str(case.get("video_filename") or "")
    return value or f"{case['video_id']}.MP4"


def expand_fps_cases(cases: list[dict[str, Any]], official_cases: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case in cases:
        source_case_id = str(case["case_id"])
        video_id = str(case["video_id"])
        source = official_cases.get(video_id)
        video_filename = _video_filename(case, source)
        fps_values = case.get("fps_values") or [1, 2, 4]
        for raw_fps in fps_values:
            video_fps = int(raw_fps)
            case_id = f"{source_case_id}__fps_{video_fps}"
            if case_id in seen:
                raise ValueError(f"duplicate expanded case_id: {case_id}")
            seen.add(case_id)
            rows.append(
                {
                    "case_id": case_id,
                    "source_case_id": source_case_id,
                    "video_id": video_id,
                    "video_filename": video_filename,
                    "input_video": str((source or {}).get("input_video") or f"{CONTAINER_WORKSPACE}/data/input/{video_filename}"),
                    "skill_output_dir": f"{CONTAINER_WORKSPACE}/outputs/openclaw_special/fps/{video_id}/{case_id}/run_01",
                    "instruction": str(case.get("instruction") or (source or {}).get("instruction") or "帮我剪辑一下这个视频"),
                    "target_duration": case.get("target_duration", (source or {}).get("target_duration")),
                    "llm_video_url": str(case.get("llm_video_url") or (source or {}).get("llm_video_url") or ""),
                    "test_type": "fps_sensitivity",
                    "priority": "special",
                    "include_in_official_score": False,
                    "video_fps": video_fps,
                }
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare OpenClaw dispatch cases for FPS sensitivity tests.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--official-cases", type=Path, default=DEFAULT_OFFICIAL_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    rows = expand_fps_cases(read_jsonl(args.cases), official_by_video_id(args.official_cases))
    write_jsonl(args.output, rows)
    print(f"FPS dispatch cases written: {args.output} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
