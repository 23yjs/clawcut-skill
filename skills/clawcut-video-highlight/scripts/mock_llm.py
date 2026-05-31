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
                "title": f"候选片段 {index + 1}",
                "summary": f"候选片段 {index + 1}：用于模拟大模型识别到的动作变化、画面转折或信息密度较高的内容。",
                "semantic_role": "模拟语义阶段",
                "expected_highlight_value": "high" if index < 2 else "medium",
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


def _make_chunk_reviews(chunks: list[dict[str, Any]], segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected_by_id = {segment["source_chunk_id"]: segment for segment in segments}
    reviews = []
    for chunk in chunks:
        selected_segment = selected_by_id.get(chunk["id"])
        should_select = selected_segment is not None
        score = 16 if should_select else 10
        reviews.append(
            {
                "chunk_id": chunk["id"],
                "summary": f"{chunk['title']} 的 mock 评分复盘。",
                "scores": {
                    "query_relevance": 4 if should_select else 2,
                    "highlight_value": 4 if should_select else 2,
                    "completeness": 4,
                    "visual_audio_evidence": 4 if should_select else 2,
                },
                "overall_score": score,
                "should_select": should_select,
                "refined_start": float(selected_segment["start"]) if selected_segment else float(chunk["start"]),
                "refined_end": float(selected_segment["end"]) if selected_segment else float(chunk["end"]),
                "reason": "mock backend 根据均匀时间轴和目标时长生成，用于验证三阶段 JSON、校验、裁剪和报告链路。",
            }
        )
    return reviews


def _default_duration_policy(duration: float, target_duration: float) -> dict[str, Any]:
    target = min(float(target_duration), duration)
    return {
        "duration_policy_mode": "bounded_auto",
        "user_specified_duration": True,
        "user_target_duration": float(target_duration),
        "recommended_duration": None,
        "selected_target_duration": target,
        "allowed_min_duration": target,
        "allowed_max_duration": target,
        "final_total_duration": None,
        "duration_policy_reason": "mock 独立调用未提供 duration_policy，按传入 target_duration 兼容处理。",
    }


def _mock_selected_target_duration(duration: float, target_duration: float, duration_policy: dict[str, Any]) -> float:
    selected = duration_policy.get("selected_target_duration")
    if selected not in (None, ""):
        return min(max(1.0, float(selected)), duration)
    if duration_policy.get("duration_policy_mode") == "llm_free" and not duration_policy.get("user_specified_duration"):
        return round(min(duration, max(1.0, duration * 0.2)), 3)
    return min(max(1.0, float(target_duration)), duration)


def _make_excluded_highlights(
    chunks: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    duration: float,
    selected_target_duration: float,
) -> list[dict[str, Any]]:
    selected_ids = {segment["source_chunk_id"] for segment in segments}
    if duration < selected_target_duration * 4 and len(chunks) <= len(segments):
        return []

    excluded = []
    for chunk in chunks:
        if chunk["id"] in selected_ids:
            continue
        start = float(chunk["start"])
        chunk_end = float(chunk["end"])
        for segment in segments:
            segment_start = float(segment["start"])
            segment_end = float(segment["end"])
            if min(chunk_end, segment_end) - max(start, segment_start) > 0.25:
                start = min(chunk_end, segment_end + 0.5)
        end = min(chunk_end, start + min(6.0, max(1.0, chunk_end - start)))
        if start >= end or end > duration:
            continue
        excluded.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "title": f"未选候选：{chunk['title']}",
                "source_chunk_id": chunk["id"],
                "score": 3.6,
                "reason": "该片段在 mock 评分中也具备一定高光价值，但最终时长容量有限，优先保留了更分散且更具代表性的片段。",
                "excluded_reason": "duration_limit",
            }
        )
        if len(excluded) >= 2:
            break
    return excluded


def generate_mock_plan(
    video_info: dict[str, Any],
    instruction: str,
    target_duration: float,
    config: dict | None = None,
) -> dict[str, Any]:
    config = config or {}
    mock_config = config.get("mock_llm", {})
    duration = float(video_info["duration"])
    duration_policy = dict(config.get("duration_policy") or _default_duration_policy(duration, target_duration))
    duration_policy.setdefault("duration_policy_mode", "bounded_auto")
    selected_target_duration = _mock_selected_target_duration(duration, target_duration, duration_policy)
    duration_policy["selected_target_duration"] = selected_target_duration
    if duration_policy.get("duration_policy_mode") == "llm_free" and not duration_policy.get("user_specified_duration"):
        duration_policy["recommended_duration"] = None
        duration_policy["allowed_min_duration"] = 0.001
        duration_policy["allowed_max_duration"] = round(duration, 3)
    chunks = _make_chunks(duration, int(mock_config.get("chunk_count", 6)))
    segments = _make_segments(
        duration,
        selected_target_duration,
        chunks,
        int(mock_config.get("max_segments", 5)),
    )
    duration_policy["final_total_duration"] = round(
        sum(float(segment["end"]) - float(segment["start"]) for segment in segments),
        3,
    )
    excluded_highlights = _make_excluded_highlights(chunks, segments, duration, selected_target_duration)

    prompt = build_highlight_prompt(video_info, instruction, selected_target_duration, duration_policy)
    return {
        "duration_policy": duration_policy,
        "video_type": _video_type_from_instruction(instruction),
        "type_confidence": 0.6,
        "user_intent": instruction,
        "highlight_definition": {
            "goal": instruction,
            "must_include": [
                "用户指令中强调的重点内容",
                "画面变化或信息密度较高的片段",
            ],
            "avoid": [
                "无信息空镜",
                "重复片段",
                "语义不完整片段",
            ],
            "selection_logic": "mock backend 不做真实视频理解，仅模拟三阶段剪辑规划结构。",
            "definition_source": "user_instruction + video_content + model_inference",
            "scoring_rubric": {
                "query_relevance": "0-5: 是否符合用户指令",
                "highlight_value": "0-5: 是否具有高光价值",
                "completeness": "0-5: 是否保留完整事件、完整话语或完整动作",
                "visual_audio_evidence": "0-5: 画面、声音、字幕或屏幕文字是否支持该片段是高光",
            },
        },
        "chunking_strategy": {
            "method": "llm_guided_semantic_chunking",
            "reason": "本地测试模式下按原视频时间轴均匀切分候选片段，仅用于验证端到端流程。",
        },
        "chunks": chunks,
        "chunk_reviews": _make_chunk_reviews(chunks, segments),
        "final_segments": segments,
        "excluded_highlights": excluded_highlights,
        "self_check": {
            "pass": True,
            "issues": [
                "mock backend 未进行真实视频理解，片段仅用于本地流程验证。",
            ],
        },
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
