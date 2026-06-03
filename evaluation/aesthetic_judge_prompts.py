from __future__ import annotations

import json
from typing import Any


AESTHETIC_JUDGE_PROMPT_VERSION = "aesthetic_judge_v2_issue_taxonomy"

AESTHETIC_JUDGE_SYSTEM_PROMPT = """你是视频成片剪辑体验评测 Judge。

你只评价最终成片的剪辑体验。

不要评价以下内容：
1. 是否真正选中了高光；
2. 是否覆盖用户关心内容；
3. 是否遵循用户指令；
4. 画面清晰度、压缩伪影、亮度或构图；
5. 是否存在黑屏、冻结画面、音频流缺失；
6. GT、Resolver 输出、selection_score 或其他已有评分。

上述问题分别由 GT 对齐评分、FFmpeg 和 DOVER 负责。

你可以观察动作边界、片段衔接、节奏、音画剪辑连续性和成片独立可观看性。你必须只返回合法 JSON，不得输出 Markdown、代码块或解释性前后缀。"""


def build_aesthetic_judge_text_prompt(
    *,
    instruction: str,
    video_type: str,
    target_duration: float | None,
    rendered_duration: float | None,
) -> str:
    payload = {
        "task": "评价最终 highlight.mp4 的剪辑体验。",
        "judge_version": AESTHETIC_JUDGE_PROMPT_VERSION,
        "allowed_inputs": {
            "instruction": instruction,
            "video_type": video_type,
            "target_duration": target_duration,
            "rendered_duration": rendered_duration,
        },
        "strict_rules": [
            "只评价剪辑体验，不评价是否真正选中了高光。",
            "不要评价用户指令遵循、语义覆盖率或高光命中率，这由 selection_score_v1 负责。",
            "不要评价画面清晰度、压缩伪影、亮度或构图，这由 DOVER 负责。",
            "不要评价黑屏、冻结画面、音频流缺失或解码错误，这由 FFmpeg 技术检查负责。",
            "严重黑屏由 FFmpeg 处理；画面模糊由 DOVER 处理；动作在投篮入框前突然结束才由 clip_boundary_completeness 处理；高光之间无解释跳转才由 transition_coherence 处理。",
            "issues 只记录剪辑体验问题，不记录高光遗漏、指令不满足、画质、黑屏、冻结或音频流缺失。",
            "issue_type 必须从给定枚举中选择，不允许自由发挥；没有明显问题时 issues 必须返回 []。",
        ],
        "issue_taxonomy": {
            "allowed_issue_types": [
                "action_truncation",
                "speech_truncation",
                "abrupt_transition",
                "severe_fragmentation",
                "redundancy",
                "pacing_too_slow",
                "pacing_too_fast",
                "audio_cut_abrupt",
                "missing_context",
                "not_standalone_watchable",
            ],
            "allowed_severities": ["low", "medium", "high"],
        },
        "score_dimensions": {
            "clip_boundary_completeness": "0-5：片段开头和结尾是否完整，口播、动作、关键结果是否被截断。",
            "transition_coherence": "0-5：片段之间是否自然，顺序是否合理，是否出现令人困惑的跳切。",
            "pacing_and_conciseness": "0-5：是否拖沓、重复，或过快导致难以理解。",
            "audio_visual_continuity": "0-5：声音是否突然中断，画面与音频是否基本连贯，是否存在明显技术性断裂。",
            "standalone_watchability": "0-5：成片能否作为独立短视频观看，重点是否清楚，整体体验是否自然。",
        },
        "required_output_schema": {
            "clip_boundary_completeness": {
                "score": 4,
                "reason": "...",
            },
            "transition_coherence": {
                "score": 3,
                "reason": "...",
            },
            "pacing_and_conciseness": {
                "score": 4,
                "reason": "...",
            },
            "audio_visual_continuity": {
                "score": 3,
                "reason": "...",
            },
            "standalone_watchability": {
                "score": 4,
                "reason": "...",
            },
            "editing_experience_score_v1": 72.0,
            "judge_confidence": 0.8,
            "judge_summary": "...",
            "manual_review_recommended": False,
            "issues": [
                {
                    "issue_type": "action_truncation",
                    "severity": "high",
                    "description": "投篮动作尚未完成，片段已经结束。",
                }
            ],
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
