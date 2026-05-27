from __future__ import annotations

from typing import Any


SYSTEM_PROMPT = """你是一名视频高光剪辑策划助手。你只能返回结构化 JSON，不能输出额外解释。"""


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
            "chunks",
            "final_segments",
        ],
    }
