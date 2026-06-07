from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_CASES = Path("data/eval/stability_cases.v2.with_llm_video_url.jsonl")
DEFAULT_OFFICIAL_CASES = Path("data/eval/cases.official.v2.jsonl")
DEFAULT_OUTPUT = Path("data/eval/generated/stability.dispatch.v2.jsonl")
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


def official_by_case_id(path: Path) -> dict[str, dict[str, Any]]:
    rows = read_jsonl(path)
    return {str(row["case_id"]): row for row in rows if row.get("case_id")}


def expand_stability_cases(cases: list[dict[str, Any]], official_cases: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case in cases:
        source_case_id = str(case["source_case_id"])
        source = official_cases.get(source_case_id)
        if not source:
            raise ValueError(f"source_case_id not found in official cases: {source_case_id}")
        repeat_count = int(case.get("repeat_count") or 3)
        for repeat_index in range(1, repeat_count + 1):
            case_id = f"{source_case_id}__repeat_{repeat_index:02d}"
            if case_id in seen:
                raise ValueError(f"duplicate expanded case_id: {case_id}")
            seen.add(case_id)
            video_id = str(source["video_id"])
            rows.append(
                {
                    "case_id": case_id,
                    "source_case_id": source_case_id,
                    "repeat_index": repeat_index,
                    "video_id": video_id,
                    "video_filename": source["video_filename"],
                    "input_video": source["input_video"],
                    "skill_output_dir": f"{CONTAINER_WORKSPACE}/outputs/openclaw_special/stability/{video_id}/{case_id}/run_01",
                    "instruction": source["instruction"],
                    "target_duration": source.get("target_duration"),
                    "llm_video_url": str(case.get("llm_video_url") or source.get("llm_video_url") or ""),
                    "test_type": "stability",
                    "priority": "special",
                    "include_in_official_score": False,
                }
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare OpenClaw dispatch cases for stability tests.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--official-cases", type=Path, default=DEFAULT_OFFICIAL_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    rows = expand_stability_cases(read_jsonl(args.cases), official_by_case_id(args.official_cases))
    write_jsonl(args.output, rows)
    print(f"Stability dispatch cases written: {args.output} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
