from __future__ import annotations

import json
from typing import Any


AESTHETIC_JUDGE_PROMPT_VERSION = "aesthetic_judge_v1"

AESTHETIC_JUDGE_SYSTEM_PROMPT = """你是视频成片审美评测 Judge。你只评价最终高光视频的观看体验，不评价选段是否命中 GT，也不能根据任何隐藏答案打分。你必须只返回合法 JSON，不得输出 Markdown、代码块或解释性前后缀。"""


def build_aesthetic_judge_text_prompt(
    *,
    instruction: str,
    video_type: str,
    target_duration: float | None,
    rendered_duration: float | None,
) -> str:
    payload = {
        "task": "评价最终 highlight.mp4 的成片观看体验。",
        "judge_version": AESTHETIC_JUDGE_PROMPT_VERSION,
        "allowed_inputs": {
            "instruction": instruction,
            "video_type": video_type,
            "target_duration": target_duration,
            "rendered_duration": rendered_duration,
        },
        "strict_rules": [
            "不要评价是否命中用户指定语义片段，这由 selection_score_v1 负责。",
            "不要根据目标时长重复扣分，这由 duration_score 负责。",
            "只根据最终视频判断片段边界、转场、节奏、音画连续性和独立观看体验。",
        ],
        "score_dimensions": {
            "clip_boundary_completeness": "0-5：片段开头和结尾是否完整，口播、动作、关键结果是否被截断。",
            "transition_coherence": "0-5：片段之间是否自然，顺序是否合理，是否出现令人困惑的跳切。",
            "pacing_and_conciseness": "0-5：是否拖沓、重复，或过快导致难以理解。",
            "audio_visual_continuity": "0-5：声音是否突然中断，画面与音频是否基本连贯，是否存在明显技术性断裂。",
            "standalone_watchability": "0-5：成片能否作为独立短视频观看，重点是否清楚，整体体验是否自然。",
        },
        "required_output_schema": {
            "judge_version": AESTHETIC_JUDGE_PROMPT_VERSION,
            "judge_status": "scored",
            "scores": {
                "clip_boundary_completeness": 0,
                "transition_coherence": 0,
                "pacing_and_conciseness": 0,
                "audio_visual_continuity": 0,
                "standalone_watchability": 0,
            },
            "strengths": ["..."],
            "issues": [
                {
                    "issue_type": "abrupt_transition",
                    "severity": "low/medium/high",
                    "start": 0,
                    "end": 0,
                    "description": "...",
                }
            ],
            "manual_review_required": False,
            "judge_confidence": 0.8,
            "overall_reason": "...",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_safe_aesthetic_judge_request_record(
    *,
    judge_video_url_sanitized: str,
    instruction: str,
    video_type: str,
    target_duration: float | None,
    rendered_duration: float | None,
    model: str,
) -> dict[str, Any]:
    return {
        "judge_prompt_version": AESTHETIC_JUDGE_PROMPT_VERSION,
        "model": model,
        "judge_video_url_sanitized": judge_video_url_sanitized,
        "instruction": instruction,
        "video_type": video_type,
        "target_duration": target_duration,
        "rendered_duration": rendered_duration,
    }
