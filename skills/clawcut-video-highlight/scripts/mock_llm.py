from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from llm_prompts import build_highlight_prompt
from utils import load_config, read_json


def _video_type_from_instruction(instruction: str) -> str:
    text = instruction.lower()
    if any(word in text for word in ["product", "unboxing", "ecommerce", "sell", "商品", "产品", "电商", "开箱", "种草", "卖点", "外观"]):
        return "电商商品视频"
    if any(word in text for word in ["game", "match", "sports", "游戏", "比赛", "运动", "赛事"]):
        return "游戏或运动视频"
    if any(word in text for word in ["talk", "speech", "podcast", "interview", "口播", "演讲", "播客", "访谈", "采访"]):
        return "口播访谈视频"
    return "通用视频"


def _make_chunks(duration: float, chunk_count: int) -> list[dict[str, Any]]:
    chunk_count = max(1, min(chunk_count, int(max(1, duration))))
    chunk_duration = duration / chunk_count
    chunks = []
    for index in range(chunk_count):
        start = round(index * chunk_duration, 3)
        end = round(duration if index == chunk_count - 1 else (index + 1) * chunk_duration, 3)
        chunks.append(
            {
                "id": f"chunk_{index + 1:02d}",
                "start": start,
                "end": end,
                "summary": f"候选片段 {index + 1}：用于模拟大模型识别到的动作变化、画面转折或信息密度较高的内容。",
                "highlight_score": round(0.9 - index * 0.07, 3),
            }
        )
    return chunks


def _make_segments(duration: float, target_duration: float, chunks: list[dict[str, Any]], max_segments: int) -> list[dict[str, Any]]:
    usable_target = min(max(1.0, target_duration), duration)
    segment_count = max(1, min(max_segments, len(chunks), int(round(usable_target / 3.0)) or 1))
    while segment_count > 1 and usable_target + 0.5 * (segment_count - 1) > duration:
        segment_count -= 1
    segment_duration = usable_target / segment_count
    extra_gap = 0.0 if segment_count == 1 else max(0.0, (duration - usable_target) / (segment_count - 1))

    segments = []
    for index in range(segment_count):
        if segment_count == 1:
            start = max(0.0, min(duration - segment_duration, duration * 0.15))
        else:
            start = index * (segment_duration + extra_gap)
        end = min(duration, start + segment_duration)
        midpoint = (start + end) / 2
        chunk = min(
            chunks,
            key=lambda item: abs(((float(item["start"]) + float(item["end"])) / 2) - midpoint),
        )
        segments.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "title": f"高光片段 {index + 1}",
                "role": "节奏铺排",
                "reason": f"该片段靠近 {chunk['id']}，模拟判断为信息密度较高、画面变化较清晰，适合作为高光候选。",
                "source_chunk_id": chunk["id"],
            }
        )

    return sorted(segments, key=lambda item: item["start"])


def generate_mock_plan(
    video_info: dict[str, Any],
    instruction: str,
    target_duration: float,
    config: dict | None = None,
) -> dict[str, Any]:
    config = config or {}
    mock_config = config.get("mock_llm", {})
    duration = float(video_info["duration"])
    chunks = _make_chunks(duration, int(mock_config.get("chunk_count", 6)))
    segments = _make_segments(
        duration,
        float(target_duration),
        chunks,
        int(mock_config.get("max_segments", 5)),
    )

    prompt = build_highlight_prompt(video_info, instruction, target_duration)
    return {
        "video_type": _video_type_from_instruction(instruction),
        "highlight_definition": {
            "goal": instruction,
            "selection_rules": [
                "优先选择动作变化明显、画面状态变化清楚或语义转折较强的片段。",
                "所有时间戳必须保持在原始视频时间轴上，方便 ffmpeg 精确裁剪。",
                "模型输出必须是结构化 JSON，供后续校验和渲染模块直接消费。",
            ],
            "target_duration": float(target_duration),
        },
        "chunking_strategy": {
            "method": "mock_uniform_timeline",
            "description": "本地测试模式下按原视频时间轴均匀切分候选片段，仅用于验证端到端流程。",
            "chunk_count": len(chunks),
        },
        "chunks": chunks,
        "final_segments": segments,
        "overall_rationale": "当前方案由 mock backend 生成，用于在没有真实模型配置时验证探测、校验、裁剪、拼接和报告输出链路。",
        "mock_metadata": {
            "prompt": prompt,
            "planner": "mock_llm_v1",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 mock 版结构化剪辑方案。")
    parser.add_argument("--video_info_json", type=Path, required=True)
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--target_duration", type=float, required=True)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    video_info = read_json(args.video_info_json)
    plan = generate_mock_plan(video_info, args.instruction, args.target_duration, config)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
