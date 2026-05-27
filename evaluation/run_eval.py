from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT_DIR / "skills" / "clawcut-video-highlight" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from metrics import compute_plan_metrics
from plan_validator import validate_plan
from utils import load_config, read_json


def main() -> int:
    parser = argparse.ArgumentParser(description="评估 ClawCut 结构化剪辑方案。")
    parser.add_argument("--plan_json", type=Path, required=True)
    parser.add_argument("--video_duration", type=float, required=True)
    parser.add_argument("--target_duration", type=float, required=True)
    args = parser.parse_args()

    config = load_config()
    plan = read_json(args.plan_json)
    metrics = compute_plan_metrics(plan, args.target_duration)
    validation = validate_plan(plan, args.video_duration, args.target_duration, config)
    print(
        json.dumps(
            {
                "metrics": metrics,
                "validation": validation,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if validation["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
