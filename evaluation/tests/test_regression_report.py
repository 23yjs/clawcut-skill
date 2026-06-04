from __future__ import annotations

import csv
import json
from pathlib import Path

from evaluation.regression_report import compare_regression, main


def _row(
    case_id: str,
    *,
    score: float = 80,
    selection: float | None = None,
    status: str = "scored_complete",
    technical: bool = True,
    fallback: bool = False,
) -> dict:
    return {
        "case_id": case_id,
        "evaluation_status": status,
        "technical_quality_passed": str(technical),
        "fallback_used": str(fallback),
        "final_score_v2": str(score),
        "selection_score_v1": str(selection if selection is not None else score),
    }


def _write_results(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "case_id",
        "evaluation_status",
        "technical_quality_passed",
        "fallback_used",
        "final_score_v2",
        "selection_score_v1",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_regression_passes_when_candidate_is_not_worse() -> None:
    summary = compare_regression(
        [_row("case_a", score=80), _row("case_b", score=72)],
        [_row("case_a", score=83), _row("case_b", score=72)],
    )
    assert summary["gate_passed"] is True
    assert summary["average_score_delta"] == 1.5
    assert summary["gate_failures"] == []


def test_regression_detects_score_fallback_technical_and_removed_case() -> None:
    summary = compare_regression(
        [_row("case_a", score=90), _row("case_b", score=80), _row("case_c", score=75), _row("case_removed", score=88)],
        [
            _row("case_a", score=70),
            _row("case_b", score=80, fallback=True),
            _row("case_c", score=75, technical=False),
        ],
    )
    assert summary["gate_passed"] is False
    assert summary["removed_case_ids"] == ["case_removed"]
    assert summary["case_score_regressions"] == 1
    assert summary["fallback_regressions"] == 1
    assert summary["technical_quality_regressions"] == 1
    assert any("removed_cases" in failure for failure in summary["gate_failures"])


def test_regression_cli_writes_outputs_and_fails_on_gate(tmp_path) -> None:
    baseline = tmp_path / "baseline.csv"
    candidate = tmp_path / "candidate.csv"
    output = tmp_path / "regression"
    _write_results(baseline, [_row("case_a", score=90)])
    _write_results(candidate, [_row("case_a", score=70)])

    assert (
        main(
            [
                "--baseline-results",
                str(baseline),
                "--candidate-results",
                str(candidate),
                "--output-dir",
                str(output),
                "--fail-on-regression",
            ]
        )
        == 1
    )
    assert (output / "regression_summary.json").exists()
    assert (output / "regression_summary.md").exists()
    assert (output / "regression_cases.csv").exists()
    summary = json.loads((output / "regression_summary.json").read_text(encoding="utf-8"))
    assert summary["gate_passed"] is False
