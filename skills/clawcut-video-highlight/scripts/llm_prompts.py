from __future__ import annotations

import json
from typing import Any


SYSTEM_PROMPT = """你是一名视频高光剪辑策划助手。你只能返回合法 JSON，不能输出 Markdown、解释文字或代码块。"""


def build_highlight_prompt(
    video_info: dict[str, Any],
    instruction: str,
    target_duration: float,
    duration_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "system": SYSTEM_PROMPT,
        "task": "按三阶段逻辑生成短视频高光剪辑方案。",
        "instruction": instruction,
        "target_duration": target_duration,
        "duration_policy": duration_policy or {},
        "video_info": video_info,
        "required_json_fields": [
            "duration_policy",
            "video_type",
            "type_confidence",
            "user_intent",
            "highlight_definition",
            "chunking_strategy",
            "chunks",
            "chunk_reviews",
            "final_segments",
            "excluded_highlights",
            "self_check",
            "overall_rationale",
        ],
    }


def build_strict_json_edit_prompt(
    video_info: dict[str, Any],
    instruction: str,
    target_duration: float,
    duration_policy: dict[str, Any] | None = None,
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
        duration_instructions = f"""
用户已经明确指定目标时长为 {float(duration_policy["selected_target_duration"]):.3f} 秒。
你必须严格围绕该时长生成 final_segments。
不要为了覆盖更多高光而明显超过目标时长。
如果候选高光很多，请优先选择最高价值、最符合用户指令、最不重复的片段。
未被选入的高光请放入 excluded_highlights，并说明 excluded_reason。
""".strip()
    else:
        duration_instructions = f"""
用户没有明确指定目标时长。
系统根据视频总时长给出的推荐时长为 {float(duration_policy["recommended_duration"]):.3f} 秒。
你可以根据视频内容密度和高光数量选择 selected_target_duration。
selected_target_duration 必须在 {float(duration_policy["allowed_min_duration"]):.3f} 到 {float(duration_policy["allowed_max_duration"]):.3f} 秒之间。
不要为了覆盖所有内容而生成过长视频。
如果你认为推荐时长合适，直接使用 recommended_duration。
""".strip()
    return f"""
你将看到一个用于视频理解的输入视频。该输入可能是：
- 用户提供的原始视频 URL；
- 或与原始视频保持相同时间轴的低码率连续 preview。

无论你看到的是哪一种输入，你输出的所有时间戳都必须基于原始视频时间轴。
最终 ffmpeg 会根据这些时间戳从原始 input_video 中裁剪和拼接，而不是从 preview 中出片。

用户指令：
{instruction}

目标总时长：
{target_duration:.3f} 秒

目标时长策略：
{duration_policy_json}

时长规则：
{duration_instructions}

原始视频信息：
{video_info_json}

请按以下三阶段完成任务，但最终只输出一个合法 JSON。

阶段 1：视频理解与任务化高光定义
- 判断这个视频大致是什么类型。
- 结合视频内容和用户 instruction，定义当前任务下什么才算高光。
- 不要把“视频类型识别”和“高光定义”割裂成两个机械步骤。
- 如果用户指令明确指定剪辑重点，必须优先采用用户指令中的重点，并结合视频内容细化。
- 如果用户指令很泛，请根据视频类型和视频内容自动推断什么是高光。
- 不要只根据单个关键词判断高光，要结合画面、动作、声音、字幕/屏幕文字、上下文和用户目标。
- 如果视频类型不在参考类型中，请根据视频实际内容自行总结高光标准，不要硬套模板。

阶段 2：语义分块与片段评分
- 按语义事件、内容阶段或表达意图切分 chunks。
- 判断每个 chunk 是否符合阶段 1 的高光定义。
- 为每个 chunk 给出评分、是否应选入最终高光、以及原因。
- 如有必要，给出 refined_start/refined_end 来微调边界，避免切在半句话、半个动作或关键结果之前。

阶段 3：全局剪辑规划与自检
- 选择 final_segments。
- 控制总时长尽量接近 selected_target_duration。
- 检查是否覆盖用户要求的重点。
- 避免重复、无关、过长铺垫、空镜、语义不完整片段。
- 避免切在半句话、半个动作或关键结果之前。
- 如果候选高光很多，输出 excluded_highlights，解释未选原因。
- 生成 self_check。

内置视频类型高光参考，仅供参考，不要硬套：
- ecommerce_product / product_showcase：
  高光通常包括商品主体清晰出现、外观细节、核心卖点、使用效果、购买转化点。
  避免空镜、重复展示、无关背景、无信息量转场。
- product_launch / course / talk：
  高光通常包括核心观点、重要结论、功能演示、效果对比、现场反馈、方法步骤。
  避免开场寒暄、长铺垫、无关参数、重复说明。
- sports / game / action：
  高光通常包括得分、击杀、关键操作、反转、胜利瞬间、欢呼或解说强反应、完整动作过程。
  避免普通移动、无结果过程、重复回放、静止画面。
- vlog / travel / lifestyle：
  高光通常包括场景变化、视觉美感、情绪高潮、故事转折、人物互动、具有传播感的片段。
  避免过长路程、无信息空镜、重复场景。
- other / unknown：
  如果视频无法归类，请根据视频实际内容自行总结高光标准，选择最能代表视频主题、信息密度最高、画面/声音证据最强、最适合作为短视频的片段。

选择 final_segments 时必须遵守：
- final_segments 总时长尽量接近 selected_target_duration；
- 宁可略短，也不要加入低质量无关片段；
- 不要因为高光很多就超过用户明确指定的时长；
- 优先选择信息密度高、与用户指令相关、画面/声音证据充分的片段；
- 避免重复片段；
- 避免过长铺垫；
- 避免空镜头；
- 避免语义不完整；
- 避免切在半句话、半个动作或关键结果之前；
- 所有 start/end 必须在原始视频时长范围内；
- start 必须小于 end；
- 每个 final segment 必须有 title、role、source_chunk_id、reason。
- 如果高光候选很多，请输出 excluded_highlights，解释未选原因；
- excluded_highlights 不参与最终剪辑，只用于报告解释；
- final_segments 才是 ffmpeg 实际裁剪的片段。

只允许输出合法 JSON。
不要输出 Markdown。
不要输出代码块。
不要输出解释性文字。
不要在 JSON 前后添加任何文本。

必须严格返回以下 JSON 结构：
{{
  "duration_policy": {{
    "user_specified_duration": true,
    "user_target_duration": 30.0,
    "recommended_duration": null,
    "selected_target_duration": 30.0,
    "allowed_min_duration": 30.0,
    "allowed_max_duration": 30.0,
    "duration_policy_reason": "为什么选择这个目标时长"
  }},
  "video_type": "ecommerce_product / product_showcase / product_launch / course / talk / sports / game / action / vlog / travel / lifestyle / other",
  "type_confidence": 0.0,
  "user_intent": "对用户指令的任务化理解",
  "highlight_definition": {{
    "goal": "本次高光剪辑目标",
    "must_include": ["必须覆盖的重点"],
    "avoid": ["需要避免的内容"],
    "selection_logic": "结合用户指令和视频内容得到的选择逻辑",
    "definition_source": "user_instruction + video_content + model_inference",
    "scoring_rubric": {{
      "query_relevance": "0-5: 是否符合用户指令",
      "highlight_value": "0-5: 是否具有高光价值",
      "completeness": "0-5: 是否保留完整事件、完整话语或完整动作",
      "visual_audio_evidence": "0-5: 画面、声音、字幕或屏幕文字是否支持该片段是高光"
    }}
  }},
  "chunking_strategy": {{
    "method": "llm_guided_semantic_chunking",
    "reason": "为什么这样分块"
  }},
  "chunks": [
    {{
      "id": "chunk_01",
      "start": 0.0,
      "end": 10.0,
      "title": "chunk 标题",
      "summary": "chunk 内容摘要",
      "semantic_role": "该 chunk 在原视频中的语义作用",
      "expected_highlight_value": "low/medium/high"
    }}
  ],
  "chunk_reviews": [
    {{
      "chunk_id": "chunk_01",
      "summary": "对该 chunk 的高光价值复盘",
      "scores": {{
        "query_relevance": 0,
        "highlight_value": 0,
        "completeness": 0,
        "visual_audio_evidence": 0
      }},
      "overall_score": 0,
      "should_select": true,
      "refined_start": 0.0,
      "refined_end": 10.0,
      "reason": "选择或不选择该 chunk 的原因"
    }}
  ],
  "final_segments": [
    {{
      "start": 0.0,
      "end": 10.0,
      "title": "最终片段标题",
      "role": "该片段在最终高光中的作用",
      "source_chunk_id": "chunk_01",
      "reason": "选择该片段的原因"
    }}
  ],
  "excluded_highlights": [
    {{
      "start": 94.0,
      "end": 110.0,
      "title": "未选候选高光标题",
      "source_chunk_id": "chunk_02",
      "score": 4.5,
      "reason": "该片段也是高质量高光，但优先级低于已选片段。",
      "excluded_reason": "duration_limit"
    }}
  ],
  "self_check": {{
    "pass": true,
    "issues": []
  }},
  "overall_rationale": "整体剪辑思路"
}}
""".strip()
