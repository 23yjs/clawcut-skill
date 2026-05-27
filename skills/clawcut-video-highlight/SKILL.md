---
name: clawcut-video-highlight
description: 面向 OpenClaw Skill 的 LLM-centric 视频高光剪辑流程；支持 Ark 真实视频多模态模型和 mock fallback，由大模型完成三阶段剪辑规划，ffmpeg 基于原始 input_video 裁剪拼接。
---

# ClawCut 视频高光剪辑

当用户希望从原始视频中剪出短视频高光，并采用“大模型负责理解与规划，ffmpeg 负责精确裁剪与拼接”的流程时，使用这个 Skill。

## 工作流程

1. 使用 `scripts/video_probe.py` 探测原始视频的时长、分辨率、帧率和音频信息。
2. 按配置使用 `scripts/make_preview.py` 生成低码率连续 preview，保持原始时间轴不变。
3. 使用 `scripts/llm_client.py` 根据配置或命令行选择 `ark` 或 `mock` backend。
4. 如果传入 `--llm_video_url`，优先把该 URL 作为模型输入；当前版本不自动上传 preview 到 TOS。
5. 模型单次调用内完成三阶段判断：视频理解与任务化高光定义、语义分块与片段评分、全局剪辑规划与自检。
6. 使用 `scripts/plan_validator.py` 校验 `final_segments` 的字段、时间戳、时长和重叠情况。
7. 使用 `scripts/ffmpeg_editor.py` 始终从原始 `input_video` 裁剪并拼接 `highlight.mp4`。
8. 输出 `segments.json`、`report.md` 和 `run.log`。

## LLM Backend

当前支持：

- `ark`：真实视频多模态模型。
- `mock`：本地测试和 fallback。

开发阶段建议保留：

```yaml
llm:
  backend: ark
  fallback_to_mock: true
```

这样没有 API Key 或 Ark 调用失败时仍可本地跑通。若要强制验证真实模型路径，可临时设置 `fallback_to_mock: false`。

## 视频输入策略

- 默认优先使用用户提供的 `--llm_video_url` 作为模型输入。
- 该 URL 可以是原视频 URL，也可以是用户自己上传的 preview URL；当前版本不自动判断 URL 指向哪种视频。
- 如果没有提供 URL，则使用本地 `preview.mp4` 的 data URL 方式，前提是模型接口支持。
- `preview.mp4` 是低码率连续视频，与原视频保持相同时间轴。
- 最终 `highlight.mp4` 始终基于 `input_video` 原视频裁剪生成。
- `video_fps` 当前默认 1，后续可针对体育、游戏、动作视频做更高 fps 或局部二次理解。

## 三阶段模型判断

阶段 1：视频理解与任务化高光定义
- 如果用户指令具体，优先服从用户指令。
- 如果用户指令泛化，根据视频类型和视频内容自动定义高光。
- 如果类型未知，自行总结高光标准，不硬套模板。

阶段 2：语义分块与片段评分
- 大模型根据高光定义对视频进行语义分块。
- 对每个 chunk 进行评分、解释和边界微调。

阶段 3：全局剪辑规划与自检
- 选择 `final_segments`。
- 控制总时长。
- 避免重复、无关、不完整片段。
- 生成 `self_check`。

## 运行示例

mock 模式：

```bash
python skills/clawcut-video-highlight/scripts/run_skill.py \
  --input_video data/input/demo.mp4 \
  --instruction "剪出 15 秒视频高光，突出商品外观和核心卖点" \
  --target_duration 15 \
  --output_dir outputs \
  --llm_backend mock
```

ark + 原视频 URL 模式：

```bash
export ARK_API_KEY=xxx
python skills/clawcut-video-highlight/scripts/run_skill.py \
  --input_video data/input/demo.mp4 \
  --instruction "剪出 30 秒高光，突出商品外观、核心卖点和使用效果" \
  --target_duration 30 \
  --output_dir outputs \
  --llm_backend ark \
  --llm_video_url "https://your-public-video-url/demo.mp4"
```

预期输出会按输入视频名自动创建子目录：

- `outputs/<video_name>/videos/highlight.mp4`
- `outputs/<video_name>/videos/preview.mp4`
- `outputs/<video_name>/reports/segments.json`
- `outputs/<video_name>/reports/report.md`
- `outputs/<video_name>/logs/run.log`

## 注意事项

- 模型输出必须是合法 JSON。
- 外部命令必须使用 `subprocess` 的 list 参数调用，不能拼接 shell 字符串。
- 当前版本不引入 ASR、抽帧、PySceneDetect、TOS SDK，也不实现 preview 自动上传 TOS。
