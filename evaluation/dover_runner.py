from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _emit(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("dover_status") == "success" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DOVER quality inference and emit JSON.")
    parser.add_argument("--video_path", type=Path, required=True)
    parser.add_argument("--repo_dir", type=Path, required=True)
    parser.add_argument("--opt_path", type=Path)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    if not args.video_path.exists():
        return _emit({"dover_status": "failed", "dover_error": f"视频不存在：{args.video_path}"})
    if not args.repo_dir.exists():
        return _emit({"dover_status": "unavailable", "dover_error": f"DOVER repo 不存在：{args.repo_dir}"})

    sys.path.insert(0, str(args.repo_dir))

    try:
        import torch  # noqa: F401
    except Exception as exc:
        return _emit({"dover_status": "unavailable", "dover_error": f"DOVER 依赖不可用：{exc}"})

    # DOVER 官方仓库不同版本的单视频入口差异较大。这里保持 JSON Runner 的稳定边界：
    # 若使用者已经准备好外部 DOVER 环境，可在此文件中按本地版本补齐推理细节；
    # 主评测进程不会解析脆弱 stdout，也不会自动下载模型或权重。
    return _emit(
        {
            "dover_status": "unavailable",
            "dover_error": "当前 DOVER Runner 未检测到可复用的官方单视频推理入口，请按 docs/dover_setup.md 配置本地 DOVER Runner。",
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
