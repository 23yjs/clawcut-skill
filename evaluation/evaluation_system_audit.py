from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED_SOURCE_FILES = {
    "official_cases": "data/eval/cases.official.v1.jsonl",
    "case_design_doc": "data/eval/CASE_DESIGN_V1.md",
    "baseline_openclaw_cases": "data/eval/baseline_openclaw_cases.v1.jsonl",
    "abnormal_cases": "data/eval/abnormal_cases.v1.jsonl",
    "stability_cases": "data/eval/stability_cases.v1.jsonl",
    "fps_cases": "data/eval/high_dynamic_fps_cases.v1.jsonl",
    "batch_dispatch": "evaluation/batch_dispatch_openclaw_baseline.py",
    "official_preflight": "evaluation/validate_official_cases.py",
    "batch_eval": "evaluation/run_batch_eval.py",
    "human_report": "evaluation/human_readable_report.py",
    "abnormal_eval": "evaluation/run_abnormal_eval.py",
    "stability_report": "evaluation/stability_report.py",
    "fps_sensitivity": "evaluation/run_fps_sensitivity_eval.py",
    "regression_report": "evaluation/regression_report.py",
    "final_delivery_report": "evaluation/final_delivery_report.py",
    "cost_model": "evaluation/config/cost_model.yaml",
    "regression_gate": "evaluation/config/regression_gate.v1.json",
}

EXPECTED_OUTPUT_FILES = {
    "official_readiness": "eval_outputs/official_v1_readiness/official_case_readiness.json",
    "official_ready_cases": "eval_outputs/official_v1_readiness/official_ready_cases.jsonl",
    "official_results": "eval_outputs/official_v1/results.csv",
    "official_summary": "eval_outputs/official_v1/summary.json",
    "human_report": "eval_outputs/official_v1/report.html",
    "human_summary": "eval_outputs/official_v1/summary.md",
    "technical_appendix": "eval_outputs/official_v1/technical_appendix.html",
    "abnormal_summary": "eval_outputs/abnormal_v1/abnormal_summary.json",
    "stability_summary": "eval_outputs/stability_v1/stability_summary.json",
    "fps_summary": "eval_outputs/fps_sensitivity_v1/fps_sensitivity_summary.json",
    "regression_summary": "eval_outputs/regression_v1/regression_summary.json",
    "final_delivery_json": "eval_outputs/final_delivery_v1/final_delivery_report.json",
    "final_delivery_md": "eval_outputs/final_delivery_v1/FINAL_EVALUATION_REPORT.md",
}

OFFICIAL_TEST_TYPES = {
    "baseline_generic",
    "specific_following",
    "conflict_exclusion",
    "duration_constrained",
    "high_dynamic",
    "long_dense_video",
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError(f"{path}: line {line_number} is not an object")
            rows.append(payload)
    return rows


def _check_required_files(repo_root: Path, mapping: dict[str, str]) -> list[dict[str, Any]]:
    checks = []
    for name, relative_path in mapping.items():
        path = repo_root / relative_path
        checks.append(
            {
                "name": name,
                "path": relative_path,
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else None,
            }
        )
    return checks


def _missing_names(checks: list[dict[str, Any]]) -> list[str]:
    return [str(item["name"]) for item in checks if not item["exists"]]


def _duplicate_ids(rows: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        case_id = str(row.get("case_id") or "")
        if case_id in seen:
            duplicates.add(case_id)
        seen.add(case_id)
    return sorted(duplicates)


def _case_inventory(repo_root: Path) -> dict[str, Any]:
    official = _read_jsonl(repo_root / "data/eval/cases.official.v1.jsonl")
    abnormal = _read_jsonl(repo_root / "data/eval/abnormal_cases.v1.jsonl")
    stability = _read_jsonl(repo_root / "data/eval/stability_cases.v1.jsonl")
    fps = _read_jsonl(repo_root / "data/eval/high_dynamic_fps_cases.v1.jsonl")
    official_types = sorted({str(row.get("test_type") or "") for row in official})
    official_priorities = sorted({str(row.get("priority") or "") for row in official})
    return {
        "official_case_count": len(official),
        "official_baseline_count": sum(1 for row in official if row.get("test_type") == "baseline_generic"),
        "official_test_types": official_types,
        "official_missing_test_types": sorted(OFFICIAL_TEST_TYPES - set(official_types)),
        "official_priorities": official_priorities,
        "official_duplicate_case_ids": _duplicate_ids(official),
        "abnormal_case_count": len(abnormal),
        "stability_case_count": len(stability),
        "fps_case_count": len(fps),
    }


def _case_inventory_errors(inventory: dict[str, Any]) -> list[str]:
    errors = []
    if inventory["official_case_count"] < 56:
        errors.append(f"official_case_count < 56: {inventory['official_case_count']}")
    if inventory["official_baseline_count"] < 31:
        errors.append(f"official_baseline_count < 31: {inventory['official_baseline_count']}")
    if inventory["official_missing_test_types"]:
        errors.append("missing official test types: " + ", ".join(inventory["official_missing_test_types"]))
    if inventory["official_duplicate_case_ids"]:
        errors.append("duplicate official case ids: " + ", ".join(inventory["official_duplicate_case_ids"]))
    if inventory["abnormal_case_count"] < 10:
        errors.append(f"abnormal_case_count < 10: {inventory['abnormal_case_count']}")
    if inventory["stability_case_count"] < 8:
        errors.append(f"stability_case_count < 8: {inventory['stability_case_count']}")
    if inventory["fps_case_count"] < 4:
        errors.append(f"fps_case_count < 4: {inventory['fps_case_count']}")
    return errors


def build_evaluation_system_audit(repo_root: Path, eval_outputs_root: Path | None = None) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    source_checks = _check_required_files(repo_root, REQUIRED_SOURCE_FILES)
    output_root = eval_outputs_root.resolve() if eval_outputs_root else repo_root
    output_checks = _check_required_files(output_root, EXPECTED_OUTPUT_FILES)
    inventory = _case_inventory(repo_root)
    source_missing = _missing_names(source_checks)
    output_missing = _missing_names(output_checks)
    inventory_errors = _case_inventory_errors(inventory)
    blocking_errors = [
        *(f"missing source artifact: {name}" for name in source_missing),
        *inventory_errors,
    ]
    if blocking_errors:
        status = "failed"
    elif output_missing:
        status = "evidence_incomplete"
    else:
        status = "ready"
    return {
        "audit_version": "evaluation_system_audit_v1",
        "status": status,
        "repo_root": str(repo_root),
        "source_checks": source_checks,
        "output_checks": output_checks,
        "case_inventory": inventory,
        "blocking_errors": blocking_errors,
        "missing_output_evidence": output_missing,
        "next_actions": _next_actions(blocking_errors, output_missing),
    }


def _next_actions(blocking_errors: list[str], output_missing: list[str]) -> list[str]:
    if blocking_errors:
        return ["先修复 source artifacts 或 case 清单错误，再运行评测。", *blocking_errors]
    if output_missing:
        return [
            "先按 evaluation/EVALUATION_SYSTEM_V1.md 的标准顺序运行真实评测命令。",
            "至少补齐 official readiness、official batch eval、abnormal、stability、fps、regression 和 final delivery 输出。",
            "当前缺失输出：" + ", ".join(output_missing),
        ]
    return ["评测体系源码与输出证据均已齐备，可进入人工复核和交付。"]


def write_audit_report(audit: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "evaluation_system_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# ClawCut Evaluation System Audit",
        "",
        f"- status: {audit['status']}",
        f"- repo_root: {audit['repo_root']}",
        "",
        "## Case Inventory",
        f"- official_case_count: {audit['case_inventory']['official_case_count']}",
        f"- official_baseline_count: {audit['case_inventory']['official_baseline_count']}",
        f"- official_test_types: {', '.join(audit['case_inventory']['official_test_types'])}",
        f"- abnormal_case_count: {audit['case_inventory']['abnormal_case_count']}",
        f"- stability_case_count: {audit['case_inventory']['stability_case_count']}",
        f"- fps_case_count: {audit['case_inventory']['fps_case_count']}",
        "",
        "## Blocking Errors",
    ]
    lines.extend(f"- {error}" for error in audit["blocking_errors"]) if audit["blocking_errors"] else lines.append("- 无")
    lines.extend(["", "## Missing Output Evidence"])
    lines.extend(f"- {name}" for name in audit["missing_output_evidence"]) if audit["missing_output_evidence"] else lines.append("- 无")
    lines.extend(["", "## Next Actions"])
    lines.extend(f"- {action}" for action in audit["next_actions"])
    (output_dir / "evaluation_system_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit ClawCut evaluation-system source artifacts and output evidence.")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--eval-outputs-root", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)
    audit = build_evaluation_system_audit(args.repo_root, args.eval_outputs_root)
    write_audit_report(audit, args.output_dir)
    print(f"评测体系自检完成：{args.output_dir} ({audit['status']})")
    if args.require_complete and audit["status"] != "ready":
        return 1
    return 0 if audit["status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
