from __future__ import annotations

import json
from typing import Any


SYSTEM_PROMPT = """你是一名视频高光剪辑策划助手。你只能返回合法 JSON，不能输出 Markdown、解释文字或代码块。"""

COMPACT_EDIT_PLAN_SCHEMA_VERSION = "compact_edit_plan_v2"


def compact_edit_plan_json_schema(
    *,
    max_final_segments: int = 24,
    max_title_chars: int = 30,
) -> dict[str, Any]:
    return {
        "name": "compact_edit_plan_v2",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["video_type", "video_type_reason", "highlight_definition", "final_segments"],
            "properties": {
                "video_type": {"type": "string", "minLength": 1},
                "video_type_reason": {"type": "string", "minLength": 1, "maxLength": 80},
                "highlight_definition": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["must_keep", "avoid"],
                    "properties": {
                        "must_keep": {
                            "type": "array",
                            "maxItems": 5,
                            "items": {"type": "string", "minLength": 1, "maxLength": 30},
                        },
                        "avoid": {
                            "type": "array",
                            "maxItems": 5,
                            "items": {"type": "string", "minLength": 1, "maxLength": 30},
                        },
                    },
                },
                "final_segments": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": int(max_final_segments),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["start", "end", "title"],
                        "properties": {
                            "start": {"type": "number", "minimum": 0},
                            "end": {"type": "number", "minimum": 0},
                            "title": {"type": "string", "minLength": 1, "maxLength": int(max_title_chars)},
                        },
                    },
                },
            },
        },
    }


def build_highlight_prompt(
    video_info: dict[str, Any],
    instruction: str,
    target_duration: float,
    duration_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "system": SYSTEM_PROMPT,
        "task": "生成紧凑版视频高光剪辑方案。",
        "schema_version": COMPACT_EDIT_PLAN_SCHEMA_VERSION,
        "instruction": instruction,
        "target_duration": target_duration,
        "duration_policy": duration_policy or {},
        "video_info": video_info,
        "required_json_fields": [
            "video_type",
            "video_type_reason",
            "highlight_definition",
            "final_segments",
        ],
    }


def build_strict_json_edit_prompt(
    video_info: dict[str, Any],
    instruction: str,
    target_duration: float,
    duration_policy: dict[str, Any] | None = None,
    *,
    max_final_segments: int = 24,
    max_title_chars: int = 30,
    repair_mode: bool = False,
) -> str:
    video_info_json = json.dumps(video_info, ensure_ascii=False, indent=2)
    duration_policy = duration_policy or {
        "user_specified_duration": True,
        "user_target_duration": target_duration,
        "recommended_duration": None,
        "selected_target_duration": target_duration,
        "allowed_min_duration": target_duration,
        "allowed_max_duration": target_duration,
        "duration_policy_reason": "兼容旧调用：未提供 duration_policy，按 target_duration 处理。",
    }
    duration_policy_json = json.dumps(duration_policy, ensure_ascii=False, indent=2)
    if duration_policy.get("user_specified_duration"):
        duration_instructions = f"用户明确指定目标时长为 {float(duration_policy['selected_target_duration']):.3f} 秒，final_segments 总时长应尽量接近该时长。"
    elif duration_policy.get("duration_policy_mode") == "llm_free":
        duration_instructions = "用户没有指定成片时长；请根据视频内容密度自行决定高光长度，不要套固定秒数，且总时长必须大于 0 并不超过原视频时长。"
    else:
        duration_instructions = (
            f"系统推荐时长为 {float(duration_policy['recommended_duration']):.3f} 秒；"
            f"可在 {float(duration_policy['allowed_min_duration']):.3f} 到 {float(duration_policy['allowed_max_duration']):.3f} 秒内选择。"
        )

    repair_notice = ""
    if repair_mode:
        repair_notice = """
上一次输出不是合法 JSON 或过长。请只返回更短的 compact_edit_plan_v2 JSON：
- 不要 Markdown；
- 不要解释；
- 不要代码块；
- 减少 final_segments 数量；
- 不要输出任何未在 schema 中声明的字段。
""".strip()

    return f"""
你将看到一个用于视频理解的输入视频。它可能是用户提供的原始视频 URL，也可能是与原始视频保持相同时间轴的 preview。
所有时间戳必须基于原始视频时间轴；最终 ffmpeg 会从原始 input_video 裁剪。

用户指令：
{instruction}

目标时长策略：
{duration_policy_json}

时长规则：
{duration_instructions}

原始视频信息：
{video_info_json}

请在内部完成视频理解、候选筛选、复核、去重和边界修正，但不要输出推理过程。

高光选择原则：
- 优先覆盖用户明确要求；
- 泛化指令时根据视频类型自行判断高光；
- 保留动作、话语、因果或结果反馈完整的片段；
- 避免重复、空镜、过长铺垫、片尾平台引导、低信息量转场；
- 体育、游戏和动作视频要保留关键动作与结果反馈；
- 教程、知识、发布会要保留关键步骤、核心观点和结论；
- 电商视频要保留商品主体、卖点、使用效果或购买价值；
- Vlog/旅行/生活方式要保留场景变化、情绪高潮、故事推进或视觉氛围。

只允许输出 compact_edit_plan_v2 JSON，结构必须严格为：
{{
  "video_type": "具体视频类型，例如 cooking_tutorial / game / ecommerce_product",
  "video_type_reason": "80 字以内说明",
  "highlight_definition": {{
    "must_keep": ["最多 5 条，每条 30 字以内"],
    "avoid": ["最多 5 条，每条 30 字以内"]
  }},
  "final_segments": [
    {{"start": 0.0, "end": 5.0, "title": "30 字以内片段标题"}}
  ]
}}

字段限制：
- final_segments 最多 {int(max_final_segments)} 条；
- title 最多 {int(max_title_chars)} 字符；
- video_type_reason 最多 80 字符；
- must_keep / avoid 最多各 5 条，每条最多 30 字符；
- start/end 必须在原视频时长范围内；
- start 必须小于 end；
- 每个片段至少 1 秒；
- 不要输出 duration_policy、chunks、chunk_reviews、source_chunk_id、reason、self_check、overall_rationale。

{repair_notice}

只返回合法 JSON object，不要在 JSON 前后添加任何文本。
""".strip()
