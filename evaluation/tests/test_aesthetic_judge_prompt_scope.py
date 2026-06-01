from __future__ import annotations

from evaluation.aesthetic_judge_prompts import AESTHETIC_JUDGE_SYSTEM_PROMPT, build_aesthetic_judge_text_prompt


def test_judge_prompt_declares_scope_boundaries():
    prompt = AESTHETIC_JUDGE_SYSTEM_PROMPT + build_aesthetic_judge_text_prompt(
        instruction="剪出高光",
        video_type="sports",
        target_duration=None,
        rendered_duration=30.0,
    )
    assert "只评价最终成片的剪辑体验" in prompt
    assert "是否真正选中了高光" in prompt
    assert "是否遵循用户指令" in prompt
    assert "画面清晰度" in prompt
    assert "黑屏" in prompt
    assert "冻结画面" in prompt
    assert "FFmpeg" in prompt
    assert "DOVER" in prompt
