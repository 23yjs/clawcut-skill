from __future__ import annotations

from typing import Any


SYSTEM_PROMPT = """你是一名视频高光剪辑策划助手。你只能返回合法 JSON，不能输出 Markdown、解释文字或代码块。"""


def build_highlight_prompt(video_info: dict[str, Any], instruction: str, target_duration: float) -> dict[str, Any]:
    return {
        "system": SYSTEM_PROMPT,
        "task": "根据用户指令生成短视频高光剪辑方案。",
        "instruction": instruction,
        "target_duration": target_duration,
        "video_info": video_info,
        "required_json_fields": [
            "video_type",
            "highlight_definition",
            "chunking_strategy",
            "chunks",
            "final_segments",
            "overall_rationale",
        ],
    }


def build_strict_json_edit_prompt(
    video_info: dict[str, Any],
    instruction: str,
    target_duration: float,
) -> str:
    return f"""
请根据用户指令和预览视频生成视频高光剪辑方案。

用户指令：
{instruction}

目标总时长：
{target_duration:.3f} 秒

原始视频信息：
{video_info}

重要约束：
1. 所有时间戳必须基于原始视频时间轴，而不是预览文件的重新编码时间轴。
2. final_segments 的总时长应尽量接近目标总时长。
3. final_segments 不能严重重叠，不能超过原始视频总时长。
4. 每个片段要有清晰的 title、role 和 reason，便于写入中文报告。
5. 只返回一个合法 JSON 对象，不要返回 Markdown，不要使用 ```json 代码块。

必须返回以下 JSON 字段：
{{
  "video_type": "视频类型",
  "highlight_definition": {{
    "goal": "高光目标",
    "selection_rules": ["选择规则 1", "选择规则 2"],
    "target_duration": {target_duration:.3f}
  }},
  "chunking_strategy": {{
    "method": "语义分块方法",
    "description": "为什么这样分块"
  }},
  "chunks": [
    {{
      "id": "chunk_01",
      "start": 0.0,
      "end": 5.0,
      "summary": "该候选片段的内容摘要",
      "highlight_score": 0.8
    }}
  ],
  "final_segments": [
    {{
      "start": 0.0,
      "end": 3.0,
      "title": "片段标题",
      "role": "该片段在高光视频中的作用",
      "reason": "选择该片段的原因"
    }}
  ],
  "overall_rationale": "整体剪辑思路"
}}
""".strip()
