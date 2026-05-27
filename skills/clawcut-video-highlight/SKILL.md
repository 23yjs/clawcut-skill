---
name: clawcut-video-highlight
description: 面向 OpenClaw Skill 的 LLM-centric 视频高光剪辑流程；第一阶段使用 mock LLM 生成结构化剪辑方案，并用 ffmpeg/ffprobe 完成本地视频处理。
---

# ClawCut 视频高光剪辑

当用户希望从原始视频中剪出短视频高光，并希望采用“大模型负责理解与规划，ffmpeg 负责精确裁剪与拼接”的流程时，使用这个 Skill。

## 工作流程

1. 使用 `scripts/video_probe.py` 探测原始视频的时长、分辨率、帧率和音频信息。
2. 使用 `scripts/make_preview.py` 生成低码率连续预览视频，保持原始时间轴不变。
3. 使用 `scripts/mock_llm.py` 生成结构化 JSON 剪辑方案。
4. 使用 `scripts/plan_validator.py` 校验 `final_segments` 的时间戳、时长和重叠情况。
5. 使用 `scripts/ffmpeg_editor.py` 按最终时间戳裁剪并拼接高光视频。
6. 使用 `scripts/run_skill.py` 输出结果文件、报告和日志。

## 运行方式

```bash
python skills/clawcut-video-highlight/scripts/run_skill.py \
  --input_video data/input/video.mp4 \
  --instruction "剪出 15 秒视频高光，突出商品外观和核心卖点" \
  --target_duration 15 \
  --output_dir outputs
```

预期输出：

- `outputs/videos/highlight.mp4`
- `outputs/reports/segments.json`
- `outputs/reports/report.md`
- `outputs/logs/run.log`

## 注意事项

- 模型输出必须是结构化 JSON。
- 外部命令必须使用 `subprocess` 的 list 参数调用，不能拼接 shell 字符串。
- 第一阶段只使用 `mock_llm.py` 模拟大模型输出，不调用真实模型 API。
