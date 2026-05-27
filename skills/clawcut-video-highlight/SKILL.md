---
name: clawcut-video-highlight
description: 面向 OpenClaw Skill 的 LLM-centric 视频高光剪辑流程；优先接入真实视频多模态模型，配置缺失时可回退 mock，并用 ffmpeg/ffprobe 完成本地视频处理。
---

# ClawCut 视频高光剪辑

当用户希望从原始视频中剪出短视频高光，并希望采用“大模型负责理解与规划，ffmpeg 负责精确裁剪与拼接”的流程时，使用这个 Skill。

## 工作流程

1. 使用 `scripts/video_probe.py` 探测原始视频的时长、分辨率、帧率和音频信息。
2. 使用 `scripts/make_preview.py` 生成低码率连续预览视频，保持原始时间轴不变。
3. 使用 `scripts/llm_client.py` 根据配置选择 Ark 或 mock backend，生成结构化 JSON 剪辑方案。
4. 使用 `scripts/plan_validator.py` 校验 `final_segments` 的时间戳、时长和重叠情况。
5. 使用 `scripts/ffmpeg_editor.py` 按最终时间戳裁剪并拼接高光视频。
6. 使用 `scripts/run_skill.py` 输出结果文件、报告和日志。

## 运行方式

```bash
python skills/clawcut-video-highlight/scripts/run_skill.py \
  --input_video data/input/video.mp4 \
  --instruction "剪出 30 秒视频高光，突出商品外观和核心卖点" \
  --target_duration 15 \
  --output_dir outputs
```

预期输出会按输入视频名自动创建子目录。例如输入 `data/input/video.mp4` 时：

- `outputs/video/videos/highlight.mp4`
- `outputs/video/reports/segments.json`
- `outputs/video/reports/report.md`
- `outputs/video/logs/run.log`

## LLM Backend

默认配置优先使用 `ark`：

```yaml
llm:
  backend: ark
  model: ""
  api_key_env: ARK_API_KEY
  base_url: ""
  fallback_to_mock: true
  temperature: 0.2
  timeout_seconds: 120
  video_input_mode: auto
  video_url: ""
  video_fps: 1
```

仓库不保存真实 API Key、模型名或服务地址。若 `model`、`base_url` 或 `ARK_API_KEY` 缺失，并且 `fallback_to_mock: true`，流程会自动使用 mock 跑通本地端到端测试。

当视频已上传到 TOS 时，优先使用 `--llm_video_url` 或 `llm.video_url` 把 URL 传给大模型；这比把本地 preview 作为 base64 data URL 直接塞进请求体更稳定。不要把视频剪辑规划模型换成 Seed3D/图生 3D 模型。

## 注意事项

- 模型输出必须是结构化 JSON。
- 外部命令必须使用 `subprocess` 的 list 参数调用，不能拼接 shell 字符串。
- `ffmpeg_editor.py` 始终基于原始 `input_video` 裁剪，预览视频只提供给模型理解和规划使用。
