from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


DEFAULT_GATE_CONFIG = {
    "max_average_score_drop": 2.0,
    "max_case_score_drop": 5.0,
    "max_removed_cases": 0,
    "max_failed_case_regressions": 0,
    "max_fallback_regressions": 0,
    "max_technical_quality_regressions": 0,
}


CSV_FIELDS = [
    "case_id",
    "baseline_status",
    "candidate_status",
    "baseline_score",
    "candidate_score",
    "score_delta",
    "selection_score_delta",
    "technical_quality_regressed",
    "fallback_regressed",
    "status_regressed",
    "case_score_regressed",
    "regression_reasons",
]


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


def _score(row: dict[str, Any]) -> float | None:
    return _to_float(row.get("final_score_v2")) or _to_float(row.get("selection_score_v1"))


def _is_failed(row: dict[str, Any] | None) -> bool:
    if not row:
        return True
    status = str(row.get("evaluation_status") or row.get("collection_status") or "")
    technical = str(row.get("technical_quality_passed")).lower()
    return status in {"failed", "batch_case_failed", "ambiguous_output"} or "failed" in status or technical == "false"


def _technical_passed(row: dict[str, Any] | None) -> bool | None:
    if not row:
        return None
    value = row.get("technical_quality_passed")
    if value in (None, ""):
        return None
    return _truthy(value)


def load_gate_config(path: Path | None) -> dict[str, float | int]:
    config = dict(DEFAULT_GATE_CONFIG)
    if path and path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("regression gate config must be a JSON object")
        config.update(payload)
    return config


def _case_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row.get("case_id") or "").strip()
        if not case_id:
            continue
        if case_id in mapped:
            raise ValueError(f"duplicate case_id in results: {case_id}")
        mapped[case_id] = row
    return mapped


def compare_regression(
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    gate_config: dict[str, float | int] | None = None,
) -> dict[str, Any]:
    gates = dict(DEFAULT_GATE_CONFIG)
    if gate_config:
        gates.update(gate_config)

    baseline = _case_map(baseline_rows)
    candidate = _case_map(candidate_rows)
    baseline_ids = set(baseline)
    candidate_ids = set(candidate)
    matched_ids = sorted(baseline_ids & candidate_ids)
    removed_ids = sorted(baseline_ids - candidate_ids)
    added_ids = sorted(candidate_ids - baseline_ids)

    case_rows = []
    failed_regressions = 0
    fallback_regressions = 0
    technical_regressions = 0
    score_regressions = 0

    for case_id in matched_ids:
        base = baseline[case_id]
        cand = candidate[case_id]
        base_score = _score(base)
        cand_score = _score(cand)
        score_delta = None if base_score is None or cand_score is None else round(cand_score - base_score, 3)
        base_selection = _to_float(base.get("selection_score_v1"))
        cand_selection = _to_float(cand.get("selection_score_v1"))
        selection_delta = None if base_selection is None or cand_selection is None else round(cand_selection - base_selection, 3)

        status_regressed = not _is_failed(base) and _is_failed(cand)
        fallback_regressed = not _truthy(base.get("fallback_used")) and _truthy(cand.get("fallback_used"))
        technical_regressed = _technical_passed(base) is True and _technical_passed(cand) is False
        case_score_regressed = score_delta is not None and score_delta < -float(gates["max_case_score_drop"])

        reasons = []
        if status_regressed:
            failed_regressions += 1
            reasons.append("candidate became failed")
        if fallback_regressed:
            fallback_regressions += 1
            reasons.append("candidate introduced fallback")
        if technical_regressed:
            technical_regressions += 1
            reasons.append("technical quality regressed")
        if case_score_regressed:
            score_regressions += 1
            reasons.append(f"score dropped by {abs(score_delta or 0):.3f}")

        case_rows.append(
            {
                "case_id": case_id,
                "baseline_status": base.get("evaluation_status") or base.get("collection_status") or "",
                "candidate_status": cand.get("evaluation_status") or cand.get("collection_status") or "",
                "baseline_score": base_score,
                "candidate_score": cand_score,
                "score_delta": score_delta,
                "selection_score_delta": selection_delta,
                "technical_quality_regressed": technical_regressed,
                "fallback_regressed": fallback_regressed,
                "status_regressed": status_regressed,
                "case_score_regressed": case_score_regressed,
                "regression_reasons": "; ".join(reasons),
            }
        )

    baseline_scores = [_score(row) for row in baseline.values()]
    candidate_scores = [_score(candidate[case_id]) for case_id in matched_ids]
    paired_scores = [
        (base, cand)
        for base, cand in zip((_score(baseline[case_id]) for case_id in matched_ids), candidate_scores)
        if base is not None and cand is not None
    ]
    average_score_delta = (
        round(sum(cand - base for base, cand in paired_scores) / len(paired_scores), 3)
        if paired_scores
        else None
    )

    gate_failures = []
    if len(removed_ids) > int(gates["max_removed_cases"]):
        gate_failures.append(f"removed_cases {len(removed_ids)} > {gates['max_removed_cases']}")
    if failed_regressions > int(gates["max_failed_case_regressions"]):
        gate_failures.append(f"failed_case_regressions {failed_regressions} > {gates['max_failed_case_regressions']}")
    if fallback_regressions > int(gates["max_fallback_regressions"]):
        gate_failures.append(f"fallback_regressions {fallback_regressions} > {gates['max_fallback_regressions']}")
    if technical_regressions > int(gates["max_technical_quality_regressions"]):
        gate_failures.append(
            f"technical_quality_regressions {technical_regressions} > {gates['max_technical_quality_regressions']}"
        )
    if average_score_delta is not None and average_score_delta < -float(gates["max_average_score_drop"]):
        gate_failures.append(f"average_score_delta {average_score_delta} < -{gates['max_average_score_drop']}")
    if score_regressions:
        gate_failures.append(f"case_score_regressions {score_regressions} > 0")

    return {
        "baseline_case_count": len(baseline),
        "candidate_case_count": len(candidate),
        "matched_case_count": len(matched_ids),
        "added_case_ids": added_ids,
        "removed_case_ids": removed_ids,
        "average_score_delta": average_score_delta,
        "baseline_score_count": sum(1 for score in baseline_scores if score is not None),
        "failed_case_regressions": failed_regressions,
        "fallback_regressions": fallback_regressions,
        "technical_quality_regressions": technical_regressions,
        "case_score_regressions": score_regressions,
        "gate_passed": not gate_failures,
        "gate_failures": gate_failures,
        "gate_config": gates,
        "cases": case_rows,
    }


def write_regression_report(summary: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "regression_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "regression_cases.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in summary["cases"]:
            writer.writerow({field: row.get(field) for field in CSV_FIELDS})

    lines = [
        "# ClawCut 版本回归报告",
        "",
        f"- gate_passed: {summary['gate_passed']}",
        f"- baseline_case_count: {summary['baseline_case_count']}",
        f"- candidate_case_count: {summary['candidate_case_count']}",
        f"- matched_case_count: {summary['matched_case_count']}",
        f"- average_score_delta: {summary['average_score_delta']}",
        f"- failed_case_regressions: {summary['failed_case_regressions']}",
        f"- fallback_regressions: {summary['fallback_regressions']}",
        f"- technical_quality_regressions: {summary['technical_quality_regressions']}",
        "",
        "## Gate Failures",
        *(f"- {failure}" for failure in summary["gate_failures"]),
        "",
        "## Regressed Cases",
        "| case_id | score_delta | status_regressed | fallback_regressed | technical_regressed | reasons |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]
    for row in summary["cases"]:
        if row["regression_reasons"]:
            lines.append(
                f"| {row['case_id']} | {row['score_delta']} | {row['status_regressed']} | "
                f"{row['fallback_regressed']} | {row['technical_quality_regressed']} | {row['regression_reasons']} |"
            )
    if not any(row["regression_reasons"] for row in summary["cases"]):
        lines.append("| 无 |  |  |  |  |  |")
    (output_dir / "regression_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two ClawCut batch evaluation results for regressions.")
    parser.add_argument("--baseline-results", type=Path, required=True)
    parser.add_argument("--candidate-results", type=Path, required=True)
    parser.add_argument("--gate-config", type=Path, default=Path("evaluation/config/regression_gate.v1.json"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args(argv)

    summary = compare_regression(
        _read_csv(args.baseline_results),
        _read_csv(args.candidate_results),
        load_gate_config(args.gate_config),
    )
    write_regression_report(summary, args.output_dir)
    print(f"版本回归报告已生成：{args.output_dir}")
    if args.fail_on_regression and not summary["gate_passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
