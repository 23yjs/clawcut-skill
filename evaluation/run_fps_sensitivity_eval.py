from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from .interval_utils import overlap_duration_between
except ImportError:  # pragma: no cover - script mode
    from interval_utils import overlap_duration_between


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    rows.append(payload)
    return rows


def _window(case: dict[str, Any]) -> dict[str, float]:
    value = case.get("critical_action_window")
    if isinstance(value, dict):
        return {"start": float(value["start"]), "end": float(value["end"])}
    if isinstance(value, str) and "-" in value:
        start, end = value.split("-", 1)
        return {"start": float(start), "end": float(end)}
    raise ValueError(f"{case.get('case_id')}: invalid critical_action_window")


def _segments(row: dict[str, Any]) -> list[dict[str, float]]:
    segments = row.get("final_segments")
    if isinstance(segments, list):
        return [{"start": float(item["start"]), "end": float(item["end"])} for item in segments]
    path = row.get("segments_json")
    if path:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return [
            {"start": float(item["start"]), "end": float(item["end"])}
            for item in payload.get("final_segments", [])
        ]
    return []


def summarize_fps_sensitivity(cases: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    results_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for row in results:
        case_id = str(row.get("case_id"))
        fps = int(float(row.get("video_fps")))
        results_by_key[(case_id, fps)] = row

    rows = []
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        window = _window(case)
        for fps in case.get("fps_values", [1, 2, 4]):
            row = results_by_key.get((str(case["case_id"]), int(fps)), {})
            final_segments = _segments(row) if row else []
            hit = overlap_duration_between(final_segments, [window]) > 0
            record = {
                "case_id": case["case_id"],
                "video_id": case.get("video_id"),
                "video_fps": int(fps),
                "critical_action_window": window,
                "critical_action_hit": hit,
                "short_highlight_missed": not hit,
                "latency_seconds": row.get("latency_seconds") or row.get("skill_llm_latency_seconds"),
                "skill_llm_total_tokens": row.get("skill_llm_total_tokens"),
                "selection_score_v1": row.get("selection_score_v1"),
                "result_available": bool(row),
            }
            rows.append(record)
            by_case[str(case["case_id"])].append(record)

    recommendations = []
    for case_id, records in sorted(by_case.items()):
        hit_fps = [record["video_fps"] for record in records if record["critical_action_hit"]]
        if hit_fps and min(hit_fps) > 1:
            recommendations.append(
                {
                    "case_id": case_id,
                    "recommendation": f"fps=1 未命中短时动作，建议评估 fps={min(hit_fps)} 或局部二次分析。",
                }
            )
        elif hit_fps:
            recommendations.append({"case_id": case_id, "recommendation": "fps=1 已命中，暂不建议全局提高 fps。"})
        else:
            recommendations.append({"case_id": case_id, "recommendation": "当前结果均未命中，需要人工复核 GT 窗口和模型输入。"})

    return {
        "case_count": len(cases),
        "result_count": len(results),
        "rows": rows,
        "recommendations": recommendations,
    }


def write_fps_report(summary: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "fps_sensitivity_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# ClawCut 高动态 FPS 敏感性评测",
        "",
        f"- case_count: {summary['case_count']}",
        f"- result_count: {summary['result_count']}",
        "",
        "| case_id | fps | hit | tokens | latency | selection_score |",
        "| --- | ---: | --- | ---: | ---: | ---: |",
    ]
    for row in summary["rows"]:
        lines.append(
            f"| {row['case_id']} | {row['video_fps']} | {row['critical_action_hit']} | "
            f"{row.get('skill_llm_total_tokens')} | {row.get('latency_seconds')} | {row.get('selection_score_v1')} |"
        )
    lines.extend(["", "## Recommendations"])
    lines.extend(f"- {item['case_id']}: {item['recommendation']}" for item in summary["recommendations"])
    (output_dir / "fps_sensitivity_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize high-dynamic FPS sensitivity results.")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--results-jsonl", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    summary = summarize_fps_sensitivity(read_jsonl(args.cases), read_jsonl(args.results_jsonl))
    write_fps_report(summary, args.output_dir)
    print(f"FPS 敏感性报告已生成：{args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
