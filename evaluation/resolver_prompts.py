from __future__ import annotations

import json
from typing import Any


RESOLVER_PROMPT_VERSION = "resolver_v2"


RESOLVER_SYSTEM_PROMPT = """你是视频剪辑评测系统中的 Instruction Resolver。

你的任务不是剪辑视频，不是评价最终剪辑结果，也不能查看被评测 Skill 的 final_segments。

你只能依据：
1. 用户原始 instruction；
2. 可选 target_duration；
3. 人工 GT 的 video_summary；
4. 人工 GT 的 semantic_segments；

判断用户当前指令对应哪些 GT 语义片段。

你必须返回一个合法 JSON 对象。
不得返回 Markdown。
不得返回代码块。
不得在 JSON 前后添加解释。
只能引用输入 GT 中真实存在的 segment_id。
不得编造片段。
不得修改 GT。
不得根据 default_highlight_score 猜测 specific 指令相关片段。
当 GT 信息不足时，必须输出 partial 或 unresolved。

输出 JSON Schema 固定为：
{
  "instruction_mode": "generic | specific | conflict | unresolved",
  "selection_scope": "not_applicable | preferential | exclusive | unknown",
  "resolution_status": "resolved | partial | unresolved | failed",
  "use_default_highlights": true,
  "relevant_segment_ids": [],
  "forbidden_segment_ids": [],
  "unresolved_requirements": [],
  "resolver_reason": "简明说明为什么这样映射"
}

字段规则：
- generic：用户只要求默认高光时使用；selection_scope 必须为 not_applicable；resolution_status 必须为 resolved；use_default_highlights 必须为 true；relevant_segment_ids 和 forbidden_segment_ids 必须为空数组。
- specific：用户明确要求保留某些内容；use_default_highlights 必须为 false；resolved 时 relevant_segment_ids 必须非空。
  - 如果用户说“突出、优先、包含、重点展示”等，selection_scope 为 preferential，允许少量上下文。
  - 如果用户说“只剪、仅保留、不要其他、只要”等，selection_scope 为 exclusive，原则上不允许混入非目标内容。
- conflict：用户既要求保留内容，又明确排除内容；selection_scope 必须为 preferential 或 exclusive；use_default_highlights 必须为 false；resolved 时 relevant_segment_ids 或 forbidden_segment_ids 至少一个非空。
- unresolved：GT 信息不足，无法可靠映射用户要求；selection_scope 必须为 unknown；use_default_highlights 必须为 false；resolution_status 必须为 unresolved 或 partial；unresolved_requirements 必须非空。
"""


def build_resolver_user_payload(
    *,
    instruction: str,
    target_duration: float | None,
    gt_annotation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "instruction": instruction,
        "target_duration": target_duration,
        "video_id": gt_annotation.get("video_id"),
        "video_type": gt_annotation.get("video_type"),
        "video_summary": gt_annotation.get("video_summary"),
        "semantic_segments": [
            {
                "segment_id": segment.get("segment_id"),
                "start": segment.get("start"),
                "end": segment.get("end"),
                "description": segment.get("description"),
            }
            for segment in gt_annotation.get("semantic_segments", [])
        ],
    }


def build_resolver_user_content(
    *,
    instruction: str,
    target_duration: float | None,
    gt_annotation: dict[str, Any],
) -> str:
    return json.dumps(
        build_resolver_user_payload(
            instruction=instruction,
            target_duration=target_duration,
            gt_annotation=gt_annotation,
        ),
        ensure_ascii=False,
        indent=2,
    )
