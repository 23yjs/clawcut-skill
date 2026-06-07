from __future__ import annotations

import argparse
import html
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


def _coerce_window(value: Any, case_id: str) -> dict[str, float]:
    if isinstance(value, dict):
        return {"start": float(value["start"]), "end": float(value["end"])}
    if isinstance(value, str) and "-" in value:
        start, end = value.split("-", 1)
        return {"start": float(start), "end": float(end)}
    raise ValueError(f"{case_id}: invalid critical_action_window")


def _windows(case: dict[str, Any]) -> list[dict[str, float]]:
    case_id = str(case.get("case_id"))
    value = case.get("critical_action_windows", case.get("critical_action_window"))
    if value is None:
        return []
    if isinstance(value, list):
        return [_coerce_window(item, case_id) for item in value]
    return [_coerce_window(value, case_id)]


def _map_container_path(path: str) -> Path:
    container_prefix = "/home/node/.openclaw/workspace"
    host_prefix = "/Users/df/DockerData/openclaw/workspace"
    if path.startswith(container_prefix + "/"):
        return Path(host_prefix + path[len(container_prefix) :])
    return Path(path)


def _segments(row: dict[str, Any]) -> list[dict[str, float]]:
    segments = row.get("final_segments")
    if isinstance(segments, list):
        return [{"start": float(item["start"]), "end": float(item["end"])} for item in segments]
    path = row.get("segments_json")
    if path:
        payload = json.loads(_map_container_path(str(path)).read_text(encoding="utf-8"))
        return [
            {"start": float(item["start"]), "end": float(item["end"])}
            for item in payload.get("final_segments", [])
        ]
    result_summary_path = row.get("result_summary")
    if result_summary_path:
        payload = json.loads(_map_container_path(str(result_summary_path)).read_text(encoding="utf-8"))
        return [
            {"start": float(item["start"]), "end": float(item["end"])}
            for item in payload.get("final_segments", [])
        ]
    return []


def summarize_fps_sensitivity(cases: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    results_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for row in results:
        case_id = str(row.get("source_case_id") or row.get("case_id"))
        fps = int(float(row.get("video_fps")))
        results_by_key[(case_id, fps)] = row

    rows = []
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        source_case_id = str(case["case_id"])
        windows = _windows(case)
        for fps in case.get("fps_values", [1, 2, 4]):
            row = results_by_key.get((source_case_id, int(fps)), {})
            final_segments = _segments(row) if row else []
            overlap_duration = overlap_duration_between(final_segments, windows) if windows else None
            hit = overlap_duration > 0 if overlap_duration is not None else None
            record = {
                "case_id": row.get("case_id") or f"{source_case_id}__fps_{int(fps)}",
                "source_case_id": source_case_id,
                "video_id": case.get("video_id"),
                "video_fps": int(fps),
                "critical_action_window": windows[0] if windows else None,
                "critical_action_windows": windows,
                "critical_action_window_count": len(windows),
                "critical_action_window_available": bool(windows),
                "critical_action_overlap_duration": overlap_duration,
                "critical_action_hit": hit,
                "short_highlight_missed": (not hit) if hit is not None else None,
                "latency_seconds": row.get("latency_seconds") or row.get("skill_llm_latency_seconds"),
                "skill_llm_total_tokens": row.get("skill_llm_total_tokens"),
                "selection_score_v1": row.get("selection_score_v1"),
                "result_available": bool(row),
            }
            rows.append(record)
            by_case[source_case_id].append(record)

    recommendations = []
    for case_id, records in sorted(by_case.items()):
        if not any(record.get("critical_action_window_available") for record in records):
            recommendations.append(
                {
                    "case_id": case_id,
                    "recommendation": "缺少 critical_action_window，无法判断短时动作是否命中；需要先补充关键动作时间窗。",
                }
            )
            continue
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
        "status": "ready",
        "case_count": len(cases),
        "source_case_count": len(cases),
        "result_count": len(results),
        "missing_critical_action_window_count": sum(
            1 for case in cases if not _windows(case)
        ),
        "rows": rows,
        "recommendations": recommendations,
    }


def _write_fps_detail_html(summary: dict[str, Any], output_dir: Path) -> None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in summary.get("rows", []):
        grouped[str(row.get("source_case_id") or row.get("case_id"))].append(row)
    blocks = []
    recommendation_by_case = {
        str(item.get("case_id")): str(item.get("recommendation") or "")
        for item in summary.get("recommendations", [])
    }
    for source_case_id, rows in sorted(grouped.items()):
        trs = []
        for row in sorted(rows, key=lambda item: int(item.get("video_fps") or 0)):
            if row.get("critical_action_hit") is None:
                hit_text = "无法判断"
            else:
                hit_text = "是" if row.get("critical_action_hit") else "否"
            trs.append(
                "<tr>"
                f"<td>{html.escape(str(row.get('video_fps')))}</td>"
                f"<td>{hit_text}</td>"
                f"<td>{html.escape(str(row.get('skill_llm_total_tokens') or '—'))}</td>"
                f"<td>{html.escape(str(row.get('latency_seconds') or '—'))}</td>"
                f"<td>{html.escape(str(row.get('selection_score_v1') or '—'))}</td>"
                "</tr>"
            )
        blocks.append(
            f"<section class=\"card\"><h2>{html.escape(source_case_id)}</h2>"
            "<table><thead><tr><th>FPS</th><th>短时动作是否命中</th><th>Skill Ark Tokens</th><th>Skill Ark 耗时</th><th>内容选择得分</th></tr></thead>"
            f"<tbody>{''.join(trs)}</tbody></table><p><strong>建议：</strong>{html.escape(recommendation_by_case.get(source_case_id, '—'))}</p></section>"
        )
    html_doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>FPS 敏感性专项</title><style>
body{{margin:0;background:#f5f7fb;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif}}main{{max-width:1200px;margin:0 auto;padding:32px 28px}}h1,h2{{color:#12365f}}.card{{background:#fff;border:1px solid #d9e1ec;border-radius:14px;padding:20px;margin-top:18px}}table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid #d9e1ec;padding:10px;text-align:left}}th{{background:#f0f4f9}}a{{color:#175cd3;text-decoration:none}}</style></head><body><main>
<h1>FPS 敏感性专项</h1>
<section class="card"><p>源 Case：{summary.get('source_case_count', 0)}；结果数：{summary.get('result_count', 0)}</p></section>
{''.join(blocks) if blocks else '<section class="card">尚无可展示结果。</section>'}
<p><a href="../../report.html">返回总览报告</a></p>
</main></body></html>"""
    (output_dir / "detail.html").write_text(html_doc, encoding="utf-8")


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
    _write_fps_detail_html(summary, output_dir)


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
