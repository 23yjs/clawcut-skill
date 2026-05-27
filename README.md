# clawcut-skill

`clawcut-skill` 是一个面向 OpenClaw Skill 的 Python 项目骨架，用于实现 LLM-centric 视频高光剪辑流程。

当前流程：

- `ffprobe` 读取原始视频的时长、分辨率、帧率和音频信息。
- `ffmpeg` 生成低码率连续预览视频。
- `llm_client.py` 根据配置选择真实 Ark backend 或本地 mock backend，生成结构化 JSON 剪辑方案。
- `plan_validator.py` 校验模型输出中的片段时间戳和总时长。
- `ffmpeg` 基于原始视频裁剪并拼接最终高光，不基于 preview 裁剪。

## 环境准备

安装 Python 依赖：

```bash
python -m pip install -r requirements.txt
```

如果本机没有 `ffmpeg` 和 `ffprobe`，请先用系统包管理器安装。

## LLM 配置

默认配置在 `skills/clawcut-video-highlight/config/default.yaml`：

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

仓库不会保存真实 API Key、模型名或服务地址。你后续需要自行填写：

- `llm.model`：火山方舟模型名
- `llm.base_url`：模型服务地址
- `ARK_API_KEY`：环境变量中的 API Key

`llm.base_url` 可以填写 Ark 根地址，例如 `https://ark.cn-beijing.volces.com/api/v3`；程序会自动补成 Chat Completions 请求地址 `/chat/completions`。也可以直接填写完整的 Chat Completions 地址。

### 模型选择建议

当前 Skill 需要“视频理解 + 结构化剪辑规划”能力，应该选择支持视频输入的 Ark Chat/多模态理解模型。

不要把 `llm.model` 换成 `doubao-seed3d-2-0-260328` 这类 Seed3D 模型；Seed3D 面向图生 3D/3D 生成任务，接口形态和目标都不是视频高光剪辑规划。

### 使用 TOS 视频 URL

经验上，视频已经在 TOS 时，优先用 URL 传给大模型，比把本地视频 base64 塞进请求体更稳：

- 请求体更小，不容易触发 `Broken pipe` 或请求体过大。
- TOS URL 更适合长视频和线上流水线。
- ffmpeg 最终仍基于本地原始视频裁剪，不会受 TOS URL 或 preview 码率影响。

如果每个视频都有自己的 TOS URL，推荐运行时传入：

```bash
python skills/clawcut-video-highlight/scripts/run_skill.py \
  --input_video data/input/ecom_cup_demo.MP4 \
  --llm_video_url "https://你的-tos-视频-url" \
  --instruction "剪出 15 秒视频高光，突出商品外观和核心卖点" \
  --target_duration 15 \
  --output_dir outputs
```

也可以把固定 URL 写在配置里：

```yaml
llm:
  video_input_mode: url
  video_url: "https://你的-tos-视频-url"
```

如果不提供 URL，`video_input_mode: auto` 会使用本地 preview 的 data URL。

设置 API Key 示例：

```bash
export ARK_API_KEY="你的 API Key"
```

切换到本地 mock：

```yaml
llm:
  backend: mock
```

默认 `backend: ark` 且 `fallback_to_mock: true`。如果 `model`、`base_url` 或 `ARK_API_KEY` 缺失，项目会自动回退到 mock，保证本地端到端流程仍能跑通。

## 输入和输出位置

推荐把原始视频放在：

```text
data/input/
```

例如：

```text
data/input/ecom_cup_demo.Mp4
```

推荐把输出目录设为：

```text
outputs/
```

运行时会按输入视频文件名自动创建子目录。例如输入 `data/input/ecom_cup_demo.MP4`，实际输出目录是：

```text
outputs/ecom_cup_demo/
```

运行后会生成：

- `outputs/ecom_cup_demo/videos/highlight.mp4`：最终高光视频
- `outputs/ecom_cup_demo/videos/preview.mp4`：低码率连续预览视频
- `outputs/ecom_cup_demo/reports/segments.json`：结构化剪辑方案
- `outputs/ecom_cup_demo/reports/report.md`：中文运行报告
- `outputs/ecom_cup_demo/logs/run.log`：运行日志
- `outputs/ecom_cup_demo/work/`：中间裁剪片段

## 运行方式

```bash
python skills/clawcut-video-highlight/scripts/run_skill.py \
  --input_video data/input/ecom_cup_demo.Mp4 \
  --instruction "剪出 15 秒视频高光，突出商品外观和核心卖点" \
  --target_duration 15 \
  --output_dir outputs
```

## 模型输出格式

所有 backend 都必须返回合法 JSON，顶层字段包括：

- `video_type`
- `highlight_definition`
- `chunking_strategy`
- `chunks`
- `final_segments`
- `overall_rationale`

每个 `final_segments` 元素必须包含：

- `start`
- `end`
- `title`
- `role`
- `reason`

## 语法检查

```bash
python -m compileall skills evaluation
```

## 评估剪辑方案

```bash
python evaluation/run_eval.py \
  --plan_json outputs/ecom_cup_demo/reports/segments.json \
  --video_duration 60 \
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
