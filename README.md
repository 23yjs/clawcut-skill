# clawcut-skill

`clawcut-skill` 是一个面向 OpenClaw Skill 的视频高光剪辑项目骨架。它采用 LLM-centric 流程：大模型负责理解视频、定义高光、语义分块、片段评分和剪辑规划；`ffmpeg` 始终负责根据最终时间戳从原始 `input_video` 裁剪和拼接。

## 当前能力

- 支持两种 LLM backend：
  - `ark`：调用火山方舟视频多模态模型。
  - `mock`：本地测试和 fallback。
- 支持用户传入 `--llm_video_url`，优先把该 URL 作为模型输入。
- 当前版本不自动上传 preview 到 TOS。
- 如果没有提供 URL，则使用本地低码率连续 `preview.mp4` 的 data URL 作为模型输入，前提是模型接口支持。
- `preview.mp4` 与原视频保持相同时间轴，但最终 `highlight.mp4` 始终基于原始 `input_video` 裁剪生成。
- `video_fps` 当前默认 1，后续可针对体育、游戏、动作类视频提高 fps 或做局部二次理解。

## 配置

默认配置在 `skills/clawcut-video-highlight/config/default.yaml`：

```yaml
llm:
  backend: ark
  model: doubao-seed-2-0-lite-260215
  api_key_env: ARK_API_KEY
  base_url: https://ark.cn-beijing.volces.com/api/v3
  fallback_to_mock: true
  temperature: 0.2
  video_input_mode: auto
  video_fps: 1
  timeout_seconds: 120

preview:
  enabled: true
  width: 640
  video_bitrate: 700k
  audio_bitrate: 64k

video_input_policy:
  prefer_original_url: true
  preview_upload_enabled: false
  preview_when_duration_gt_sec: 600
  preview_when_size_gt_mb: 300
```

设置 Ark API Key：

```bash
export ARK_API_KEY="你的 API Key"
```

`llm.base_url` 可以填写 Ark 根地址，例如 `https://ark.cn-beijing.volces.com/api/v3`；程序会自动补成 Chat Completions 请求地址 `/chat/completions`。

## 三阶段模型判断逻辑

阶段 1：视频理解与任务化高光定义
- 如果用户指令具体，优先服从用户指令。
- 如果用户指令泛化，根据视频类型和视频内容自动定义高光。
- 如果类型未知，自行总结高光标准，不硬套模板。

阶段 2：语义分块与片段评分
- 大模型根据高光定义对视频进行语义分块。
- 对每个 chunk 进行评分、解释和边界微调。

阶段 3：全局剪辑规划与自检
- 选择 `final_segments`。
- 控制总时长接近 `target_duration`。
- 避免重复、无关、不完整片段。
- 生成 `self_check`。

## 输出 JSON

模型必须返回合法 JSON，核心字段包括：

- `video_type`
- `type_confidence`
- `user_intent`
- `highlight_definition`
- `chunking_strategy`
- `chunks`
- `chunk_reviews`
- `final_segments`
- `self_check`
- `overall_rationale`

`final_segments` 是最终裁剪依据，每个对象必须包含：

- `start`
- `end`
- `title`
- `role`
- `source_chunk_id`
- `reason`

## 输入和输出位置

推荐把原始视频放在：

```text
data/input/
```

推荐把输出根目录设为：

```text
outputs/
```

运行时会按输入视频文件名自动创建子目录。例如输入 `data/input/ecom_cup_demo.MP4`，实际输出目录是：

```text
outputs/ecom_cup_demo/
```

最终输出包括：

- `outputs/<video_name>/videos/highlight.mp4`
- `outputs/<video_name>/videos/preview.mp4`
- `outputs/<video_name>/reports/segments.json`
- `outputs/<video_name>/reports/report.md`
- `outputs/<video_name>/logs/run.log`
- `outputs/<video_name>/work/`

## 示例命令

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

## 检查

```bash
python -m compileall skills evaluation
```

评估已有剪辑方案：

```bash
python evaluation/run_eval.py \
  --plan_json outputs/ecom_cup_demo/reports/segments.json \
  --video_duration 233.289002 \
  --target_duration 15
```

## 项目结构

```text
skills/clawcut-video-highlight/
  SKILL.md
  config/default.yaml
  schemas/edit_plan.schema.json
  scripts/
    run_skill.py
    video_probe.py
    make_preview.py
    llm_client.py
    mock_llm.py
    plan_validator.py
    ffmpeg_editor.py
    llm_prompts.py
    utils.py
evaluation/
  metrics.py
  run_eval.py
```
