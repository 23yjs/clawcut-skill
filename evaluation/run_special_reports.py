from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_OUTPUT_ROOT = Path("eval_outputs/official_v2")
DEFAULT_ABNORMAL_CASES = Path("data/eval/abnormal_cases.v1.jsonl")
DEFAULT_RESOLVER_CASES = Path("data/eval/resolver_semantics_cases.v1.with_llm_video_url.jsonl")
DEFAULT_FPS_CASES = Path("data/eval/high_dynamic_fps_cases.v2.with_llm_video_url.jsonl")
DEFAULT_STABILITY_CASES = Path("data/eval/stability_cases.v2.with_llm_video_url.jsonl")
DEFAULT_OFFICIAL_CASES = Path("data/eval/cases.official.v2.jsonl")
DEFAULT_GT_DIR = Path("data/eval")


def run_command(args: list[str]) -> None:
    subprocess.run(args, check=True)


def mode_abnormal(args: argparse.Namespace) -> None:
    output_dir = args.output_root / "special_reports" / "abnormal"
    run_command([
        sys.executable,
        "-m",
        "evaluation.run_abnormal_suite",
        "--cases",
        str(args.abnormal_cases),
        "--output-dir",
        str(output_dir),
    ])
    run_command([
        sys.executable,
        "-m",
        "evaluation.run_abnormal_eval",
        "--cases",
        str(args.abnormal_cases),
        "--results-jsonl",
        str(output_dir / "abnormal_results.jsonl"),
        "--output-dir",
        str(output_dir),
    ])


def mode_resolver(args: argparse.Namespace) -> None:
    run_command([
        sys.executable,
        "-m",
        "evaluation.run_resolver_semantics_eval",
        "--cases",
        str(args.resolver_cases),
        "--gt-dir",
        str(args.gt_dir),
        "--output-dir",
        str(args.output_root / "special_reports" / "resolver"),
    ])


def mode_fps_prepare(args: argparse.Namespace) -> None:
    run_command([
        sys.executable,
        "-m",
        "evaluation.prepare_fps_dispatch_cases",
        "--cases",
        str(args.fps_cases),
        "--official-cases",
        str(args.official_cases),
        "--output",
        str(args.fps_dispatch_output),
    ])


def mode_fps_report(args: argparse.Namespace) -> None:
    if args.results_jsonl is None:
        raise SystemExit("--results-jsonl is required for fps-report")
    run_command([
        sys.executable,
        "-m",
        "evaluation.run_fps_sensitivity_eval",
        "--cases",
        str(args.fps_cases),
        "--results-jsonl",
        str(args.results_jsonl),
        "--output-dir",
        str(args.output_root / "special_reports" / "fps"),
    ])


def mode_stability_prepare(args: argparse.Namespace) -> None:
    run_command([
        sys.executable,
        "-m",
        "evaluation.prepare_stability_dispatch_cases",
        "--cases",
        str(args.stability_cases),
        "--official-cases",
        str(args.official_cases),
        "--output",
        str(args.stability_dispatch_output),
    ])


def mode_stability_report(args: argparse.Namespace) -> None:
    if args.results_jsonl is None:
        raise SystemExit("--results-jsonl is required for stability-report")
    run_command([
        sys.executable,
        "-m",
        "evaluation.stability_report",
        "--results-jsonl",
        str(args.results_jsonl),
        "--output-dir",
        str(args.output_root / "special_reports" / "stability"),
    ])


def mode_refresh_report(args: argparse.Namespace) -> None:
    run_command([
        sys.executable,
        "-m",
        "evaluation.run_official_eval_report_v2",
        "--mode",
        "report-only",
        "--cases",
        str(args.official_cases),
        "--gt-dir",
        str(args.gt_dir),
        "--output-dir",
        str(args.output_root),
    ])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run or prepare ClawCut special reports.")
    parser.add_argument(
        "--mode",
        required=True,
        choices=[
            "abnormal",
            "resolver",
            "fps-prepare",
            "fps-report",
            "stability-prepare",
            "stability-report",
            "refresh-report",
        ],
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--official-cases", type=Path, default=DEFAULT_OFFICIAL_CASES)
    parser.add_argument("--gt-dir", type=Path, default=DEFAULT_GT_DIR)
    parser.add_argument("--abnormal-cases", type=Path, default=DEFAULT_ABNORMAL_CASES)
    parser.add_argument("--resolver-cases", type=Path, default=DEFAULT_RESOLVER_CASES)
    parser.add_argument("--fps-cases", type=Path, default=DEFAULT_FPS_CASES)
    parser.add_argument("--stability-cases", type=Path, default=DEFAULT_STABILITY_CASES)
    parser.add_argument("--fps-dispatch-output", type=Path, default=Path("data/eval/generated/high_dynamic_fps.dispatch.v2.jsonl"))
    parser.add_argument("--stability-dispatch-output", type=Path, default=Path("data/eval/generated/stability.dispatch.v2.jsonl"))
    parser.add_argument("--results-jsonl", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dispatch = {
        "abnormal": mode_abnormal,
        "resolver": mode_resolver,
        "fps-prepare": mode_fps_prepare,
        "fps-report": mode_fps_report,
        "stability-prepare": mode_stability_prepare,
        "stability-report": mode_stability_report,
        "refresh-report": mode_refresh_report,
    }
    dispatch[args.mode](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
