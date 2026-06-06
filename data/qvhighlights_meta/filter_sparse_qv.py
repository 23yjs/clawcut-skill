#!/usr/bin/env python3
"""
从 QVHighlights 官方标注中筛选高光稀疏候选视频。

注意：
1. density 表示 query 相关窗口的并集时长 / 视频总时长。
2. 这只是候选筛选指标，不等价于 ClawCut 的默认高光密度。
3. 最终纳入数据集前，必须人工观看并重新标注。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable


INPUT_FILES = [
    Path("highlight_train_release.jsonl"),
    Path("highlight_val_release.jsonl"),
]

OUTPUT_FILE = Path("sparse_candidates.csv")

# 第一轮建议先严格筛选。候选太少时再放宽。
MAX_DENSITY = 0.12
MIN_DURATION = 90
MAX_DURATION = 240
MIN_MAX_MEAN_SALIENCY = 3.0
MAX_RESULTS = 200


def merge_intervals(
    intervals: Iterable[list[float]],
) -> list[tuple[float, float]]:
    cleaned = sorted(
        (float(start), float(end))
        for start, end in intervals
        if float(end) > float(start)
    )

    merged: list[tuple[float, float]] = []

    for start, end in cleaned:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue

        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))

    return merged


def calculate_density(
    relevant_windows: list[list[float]],
    duration: float,
) -> tuple[float, float]:
    if duration <= 0:
        return 1.0, 0.0

    merged = merge_intervals(relevant_windows)
    relevant_duration = sum(end - start for start, end in merged)

    return relevant_duration / duration, relevant_duration


def calculate_max_mean_saliency(
    saliency_scores: list[list[float]],
) -> float:
    means = [
        sum(scores) / len(scores)
        for scores in saliency_scores
        if scores
    ]

    return max(means, default=0.0)


def parse_vid(vid: str) -> tuple[str, float, float]:
    youtube_id, start, end = vid.rsplit("_", 2)

    return youtube_id, float(start), float(end)


def build_preview_url(vid: str) -> str:
    youtube_id, start, end = parse_vid(vid)

    return (
        f"https://www.youtube.com/embed/{youtube_id}"
        f"?start={int(start)}"
        f"&end={int(end)}"
        f"&version=3"
    )


def build_hf_filename(vid: str) -> str:
    return f"moment_retrieval/qvhighlight/videos/{vid}.mp4"


def iter_rows(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()

            if line:
                yield json.loads(line)


def main() -> None:
    candidates: list[dict] = []

    for path in INPUT_FILES:
        if not path.exists():
            raise FileNotFoundError(f"缺少标注文件：{path}")

        for row in iter_rows(path):
            duration = float(row["duration"])
            relevant_windows = row.get("relevant_windows", [])
            saliency_scores = row.get("saliency_scores", [])

            if not relevant_windows:
                continue

            if not (MIN_DURATION <= duration <= MAX_DURATION):
                continue

            density, relevant_duration = calculate_density(
                relevant_windows,
                duration,
            )

            if density > MAX_DENSITY:
                continue

            max_mean_saliency = calculate_max_mean_saliency(
                saliency_scores,
            )

            if max_mean_saliency < MIN_MAX_MEAN_SALIENCY:
                continue

            candidates.append(
                {
                    "qid": row["qid"],
                    "vid": row["vid"],
                    "query": row["query"],
                    "duration_seconds": round(duration, 2),
                    "relevant_duration_seconds": round(
                        relevant_duration,
                        2,
                    ),
                    "density": round(density, 4),
                    "max_mean_saliency": round(
                        max_mean_saliency,
                        3,
                    ),
                    "relevant_windows": json.dumps(
                        relevant_windows,
                        ensure_ascii=False,
                    ),
                    "preview_url": build_preview_url(row["vid"]),
                    "hf_filename": build_hf_filename(row["vid"]),
                }
            )

    candidates.sort(
        key=lambda row: (
            row["density"],
            -row["max_mean_saliency"],
            -row["duration_seconds"],
        )
    )

    candidates = candidates[:MAX_RESULTS]

    if not candidates:
        raise RuntimeError(
            "没有找到候选。可以将 MAX_DENSITY 调整为 0.15，"
            "或者将 MIN_MAX_MEAN_SALIENCY 调整为 2.7。"
        )

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=list(candidates[0].keys()),
        )

        writer.writeheader()
        writer.writerows(candidates)

    print(f"已输出 {len(candidates)} 条候选：{OUTPUT_FILE}")


if __name__ == "__main__":
    main()