from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_COST_MODEL = {
    "prompt_token_usd_per_1k": 0.0,
    "completion_token_usd_per_1k": 0.0,
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    rows.append(payload)
    return rows


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def load_cost_model(path: Path | None) -> dict[str, float]:
    if path is None or not path.exists():
        return dict(DEFAULT_COST_MODEL)
    values = dict(DEFAULT_COST_MODEL)
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        try:
            values[key] = float(value.strip())
        except ValueError:
            continue
    return values


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _std(values: list[float]) -> float | None:
    if len(values) < 2:
        return 0.0 if values else None
    mean = sum(values) / len(values)
    return round(math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)), 3)


def _estimate_cost(row: dict[str, Any], cost_model: dict[str, float]) -> float | None:
    prompt = _to_float(row.get("skill_llm_prompt_tokens"))
    completion = _to_float(row.get("skill_llm_completion_tokens"))
    total = _to_float(row.get("skill_llm_total_tokens"))
    if prompt is None and completion is None and total is None:
        return None
    prompt = prompt or 0.0
    completion = completion or 0.0
    if prompt == 0 and completion == 0 and total is not None:
        prompt = total
    return round(
        prompt * cost_model.get("prompt_token_usd_per_1k", 0.0) / 1000
        + completion * cost_model.get("completion_token_usd_per_1k", 0.0) / 1000,
        6,
    )


def summarize_stability(rows: list[dict[str, Any]], cost_model: dict[str, float]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("case_id") or row.get("video_id") or "unknown")].append(row)

    cases = []
    for case_id, attempts in sorted(grouped.items()):
        statuses = [str(row.get("collection_status") or row.get("evaluation_status") or "") for row in attempts]
        official_success = sum(1 for status in statuses if status in {"official_success", "scored_complete", "selection_scored_aesthetic_pending"})
        success = sum(1 for status in statuses if status not in {"failed", "ambiguous_output", "batch_case_failed"})
        skill_fallback = sum(1 for row in attempts if _truthy(row.get("fallback_used")) or "diagnostic_skill_fallback" == row.get("collection_status"))
        openclaw_fallback = sum(1 for row in attempts if row.get("collection_status") == "diagnostic_openclaw_fallback")
        latencies = [
            value
            for value in (
                _to_float(row.get("elapsed_seconds")) or _to_float(row.get("skill_llm_latency_seconds"))
                for row in attempts
            )
            if value is not None
        ]
        tokens = [
            value
            for value in (_to_float(row.get("skill_llm_total_tokens")) for row in attempts)
            if value is not None
        ]
        scores = [
            value
            for value in (
                _to_float(row.get("final_score_v2")) or _to_float(row.get("selection_score_v1"))
                for row in attempts
            )
            if value is not None
        ]
        costs = [
            value
            for value in (_estimate_cost(row, cost_model) for row in attempts)
            if value is not None
        ]
        highlight_paths = {str(row.get("highlight_video") or "") for row in attempts if row.get("highlight_video")}
        cases.append(
            {
                "case_id": case_id,
                "attempt_count": len(attempts),
                "success_rate": round(success / len(attempts), 3),
                "official_success_rate": round(official_success / len(attempts), 3),
                "skill_fallback_rate": round(skill_fallback / len(attempts), 3),
                "openclaw_fallback_rate": round(openclaw_fallback / len(attempts), 3),
                "avg_latency_seconds": _mean(latencies),
                "max_latency_seconds": round(max(latencies), 3) if latencies else None,
                "avg_skill_llm_total_tokens": _mean(tokens),
                "estimated_cost": round(sum(costs), 6) if costs else None,
                "selection_score_mean": _mean(scores),
                "selection_score_std": _std(scores),
                "final_segments_changed": len(highlight_paths) > 1,
            }
        )
    return {
        "case_count": len(cases),
        "attempt_count": len(rows),
        "cases": cases,
        "overall": {
            "official_success_rate": _mean([case["official_success_rate"] for case in cases]),
            "skill_fallback_rate": _mean([case["skill_fallback_rate"] for case in cases]),
            "openclaw_fallback_rate": _mean([case["openclaw_fallback_rate"] for case in cases]),
            "estimated_cost": round(sum(case["estimated_cost"] or 0 for case in cases), 6),
        },
    }


def write_stability_report(summary: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "stability_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# ClawCut 稳定性与成本报告",
        "",
        f"- case_count: {summary['case_count']}",
        f"- attempt_count: {summary['attempt_count']}",
        f"- official_success_rate: {summary['overall']['official_success_rate']}",
        f"- skill_fallback_rate: {summary['overall']['skill_fallback_rate']}",
        f"- openclaw_fallback_rate: {summary['overall']['openclaw_fallback_rate']}",
        f"- estimated_cost: {summary['overall']['estimated_cost']}",
        "",
        "| case_id | attempts | official_success_rate | skill_fallback_rate | avg_latency_seconds | avg_tokens | score_std |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for case in summary["cases"]:
        lines.append(
            f"| {case['case_id']} | {case['attempt_count']} | {case['official_success_rate']} | "
            f"{case['skill_fallback_rate']} | {case['avg_latency_seconds']} | "
            f"{case['avg_skill_llm_total_tokens']} | {case['selection_score_std']} |"
        )
    (output_dir / "stability_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize ClawCut stability, latency, token and cost metrics.")
    parser.add_argument("--results-jsonl", type=Path)
    parser.add_argument("--results-csv", type=Path)
    parser.add_argument("--cost-model", type=Path, default=Path("evaluation/config/cost_model.yaml"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.results_jsonl and not args.results_csv:
        parser.error("one of --results-jsonl or --results-csv is required")
    rows = _read_jsonl(args.results_jsonl) if args.results_jsonl else _read_csv(args.results_csv)
    summary = summarize_stability(rows, load_cost_model(args.cost_model))
    write_stability_report(summary, args.output_dir)
    print(f"稳定性报告已生成：{args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
