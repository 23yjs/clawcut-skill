from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUTOMATED_RETRY_ACTIONS = {
    "rerun_skill",
    "retry_judge",
    "retry_judge_video_upload",
    "retry_resolver",
}

PLAN_ACTIONS = [
    "rerun_skill",
    "retry_judge",
    "retry_judge_video_upload",
    "retry_resolver",
    "inspect_technical_quality",
    "manual_review_required",
    "manual_review_recommended",
]

ACTION_LABELS = {
    "rerun_skill": "重新运行 Skill",
    "retry_judge": "只重试 Judge",
    "retry_judge_video_upload": "重新上传 TOS 后重试 Judge",
    "retry_resolver": "只重试 Resolver 和后续评测",
    "inspect_technical_quality": "仅输出清单",
    "manual_review_required": "仅输出清单",
    "manual_review_recommended": "仅输出清单",
}


@dataclass(frozen=True)
class RetryPlan:
    plan_dir: Path
    plan: dict[str, Any]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    rows.append(payload)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def case_id_of(row: dict[str, Any]) -> str:
    return str(row.get("case_id") or "")


def cases_by_id(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {case_id_of(case): case for case in cases if case_id_of(case)}


def load_case_index(output_dir: Path) -> list[dict[str, Any]]:
    summary = read_json(output_dir / "summary.json")
    case_index = summary.get("case_index")
    if isinstance(case_index, list):
        return [row for row in case_index if isinstance(row, dict)]
    rows: list[dict[str, Any]] = []
    for result_path in sorted((output_dir / "runs").glob("*/evaluation_result.json")):
        payload = read_json(result_path)
        if payload:
            rows.append(_row_from_result(payload))
    return rows


def _row_from_result(result: dict[str, Any]) -> dict[str, Any]:
    review = result.get("manual_review") if isinstance(result.get("manual_review"), dict) else {}
    return {
        "case_id": result.get("case_id"),
        "video_id": result.get("video_id"),
        "evaluation_status": result.get("evaluation_status"),
        "official_eligible": (result.get("official_score_eligibility") or {}).get("eligible"),
        "recommended_action": result.get("recommended_action"),
        "manual_review_required": review.get("required"),
        "manual_review_recommended": review.get("recommended"),
        "manual_review_reasons": "；".join(str(item) for item in result.get("manual_review_reasons", []) or []),
        "content_selection_attribution": result.get("content_selection_attribution"),
    }


def unique_plan_dir(output_dir: Path, *, now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    root = output_dir / "retry_plans"
    candidate = root / timestamp
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = root / f"{timestamp}_{suffix:02d}"
    return candidate


def build_plan(output_dir: Path, cases_path: Path, *, plan_dir: Path | None = None) -> RetryPlan:
    cases = read_jsonl(cases_path)
    case_lookup = cases_by_id(cases)
    case_index = load_case_index(output_dir)
    plan_dir = plan_dir or unique_plan_dir(output_dir)
    buckets: dict[str, list[dict[str, Any]]] = {action: [] for action in PLAN_ACTIONS}
    skipped_success: list[str] = []
    unknown_action: list[dict[str, Any]] = []

    for row in case_index:
        case_id = case_id_of(row)
        action = str(row.get("recommended_action") or "none")
        if row.get("official_eligible") is True and action in {"none", ""}:
            skipped_success.append(case_id)
            continue
        if action in buckets:
            buckets[action].append(row)
        elif action not in {"none", ""}:
            unknown_action.append(row)

    rerun_skill_cases = _cases_for_action(buckets["rerun_skill"], case_lookup)
    retry_eval_actions = ["rerun_skill", "retry_judge", "retry_judge_video_upload", "retry_resolver"]
    retry_eval_cases = _dedupe_cases(
        case
        for action in retry_eval_actions
        for case in _cases_for_action(buckets[action], case_lookup)
    )

    plan = {
        "plan_schema_version": "retry_plan_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(output_dir),
        "cases": str(cases_path),
        "plan_dir": str(plan_dir),
        "counts": {
            action: len(rows)
            for action, rows in buckets.items()
        },
        "skipped_success_count": len(skipped_success),
        "skipped_success_case_ids": skipped_success,
        "unknown_action_case_ids": [case_id_of(row) for row in unknown_action],
        "actions": {
            action: {
                "label": ACTION_LABELS[action],
                "case_ids": [case_id_of(row) for row in rows],
            }
            for action, rows in buckets.items()
        },
        "files": {
            "retry_plan": str(plan_dir / "retry_plan.json"),
            "rerun_skill_cases": str(plan_dir / "rerun_skill_cases.jsonl"),
            "retry_eval_cases": str(plan_dir / "retry_eval_cases.jsonl"),
            "manual_review_required": str(plan_dir / "manual_review_required.csv"),
            "manual_review_recommended": str(plan_dir / "manual_review_recommended.csv"),
            "retry_summary": str(plan_dir / "retry_summary.md"),
        },
    }
    plan_dir.mkdir(parents=True, exist_ok=False)
    write_plan_files(
        plan_dir=plan_dir,
        plan=plan,
        rerun_skill_cases=rerun_skill_cases,
        retry_eval_cases=retry_eval_cases,
        manual_required=buckets["manual_review_required"],
        manual_recommended=buckets["manual_review_recommended"],
    )
    return RetryPlan(plan_dir=plan_dir, plan=plan)


def _cases_for_action(rows: list[dict[str, Any]], case_lookup: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    cases = []
    for row in rows:
        case = case_lookup.get(case_id_of(row))
        if case:
            cases.append(case)
    return cases


def _dedupe_cases(cases: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case in cases:
        case_id = case_id_of(case)
        if not case_id or case_id in seen:
            continue
        seen.add(case_id)
        rows.append(case)
    return rows


def write_plan_files(
    *,
    plan_dir: Path,
    plan: dict[str, Any],
    rerun_skill_cases: list[dict[str, Any]],
    retry_eval_cases: list[dict[str, Any]],
    manual_required: list[dict[str, Any]],
    manual_recommended: list[dict[str, Any]],
) -> None:
    (plan_dir / "retry_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_jsonl(plan_dir / "rerun_skill_cases.jsonl", rerun_skill_cases)
    write_jsonl(plan_dir / "retry_eval_cases.jsonl", retry_eval_cases)
    write_manual_csv(plan_dir / "manual_review_required.csv", manual_required)
    write_manual_csv(plan_dir / "manual_review_recommended.csv", manual_recommended)
    (plan_dir / "retry_summary.md").write_text(retry_summary_markdown(plan), encoding="utf-8")


def write_manual_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "case_id",
        "video_id",
        "evaluation_status",
        "recommended_action",
        "manual_review_reasons",
        "content_selection_attribution",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def retry_summary_markdown(plan: dict[str, Any]) -> str:
    lines = [
        "# ClawCut 失败用例恢复计划",
        "",
        f"- output_dir: `{plan['output_dir']}`",
        f"- cases: `{plan['cases']}`",
        f"- plan_dir: `{plan['plan_dir']}`",
        f"- skipped_success_count: {plan['skipped_success_count']}",
        "",
        "| 处置类型 | 数量 |",
        "| --- | ---: |",
    ]
    for action in PLAN_ACTIONS:
        item = plan["actions"][action]
        lines.append(f"| {item['label']} | {len(item['case_ids'])} |")
    lines.extend(["", "## 输出文件"])
    lines.extend(f"- `{value}`" for value in plan["files"].values())
    return "\n".join(lines) + "\n"


def run_skill_reruns(plan_dir: Path, *, resume: bool, max_attempts: int) -> None:
    subprocess.run(
        [
            sys.executable,
            "evaluation/batch_dispatch_openclaw_official.py",
            "--cases",
            str(plan_dir / "rerun_skill_cases.jsonl"),
            "--max-attempts",
            str(max_attempts),
            *(["--resume"] if resume else []),
        ],
        check=True,
    )


def run_eval_retries(
    plan_dir: Path,
    *,
    output_dir: Path,
    gt_dir: Path,
    resume: bool,
) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.run_official_eval_report_v2",
            "--mode",
            "evaluate",
            "--cases",
            str(plan_dir / "retry_eval_cases.jsonl"),
            "--gt-dir",
            str(gt_dir),
            "--output-dir",
            str(output_dir),
            "--retry-failed",
            *(["--resume"] if resume else []),
        ],
        check=True,
    )


def rebuild_report(*, output_dir: Path, cases: Path, gt_dir: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.run_official_eval_report_v2",
            "--mode",
            "report-only",
            "--cases",
            str(cases),
            "--gt-dir",
            str(gt_dir),
            "--output-dir",
            str(output_dir),
        ],
        check=True,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and optionally execute ClawCut retry plans.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--gt-dir", type=Path, default=Path("data/eval"))
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--run-skill-reruns", action="store_true")
    parser.add_argument("--run-eval-retries", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    retry_plan = build_plan(args.output_dir, args.cases)
    should_run_skill = args.all or args.run_skill_reruns
    should_run_eval = args.all or args.run_eval_retries
    if args.plan_only or args.dry_run:
        should_run_skill = False
        should_run_eval = False
    if should_run_skill:
        run_skill_reruns(retry_plan.plan_dir, resume=args.resume, max_attempts=args.max_attempts)
    if should_run_eval:
        run_eval_retries(
            retry_plan.plan_dir,
            output_dir=args.output_dir,
            gt_dir=args.gt_dir,
            resume=args.resume,
        )
    if args.all and not args.dry_run and not args.plan_only:
        rebuild_report(output_dir=args.output_dir, cases=args.cases, gt_dir=args.gt_dir)
    print(f"retry plan written: {retry_plan.plan_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
