---
name: clawcut-video-highlight
description: 用于从输入视频中生成指令引导的视频高光剪辑。Skill 由大模型规划高光片段，并由 ffmpeg 基于原始视频完成裁剪和拼接。
---

# ClawCut 视频高光剪辑 Skill

这份文档是给 OpenClaw/Agent 使用的调用协议。Agent 只负责理解用户需求、抽取参数、调用 `scripts/run_skill.py`，不要自己生成剪辑时间戳，也不要绕过主入口直接调用 `ffmpeg`。

## 什么时候使用

当用户有以下需求时，使用本 Skill：

- 想从视频中剪出高光片段。
- 想根据自然语言指令生成短视频剪辑。
- 想提取商品卖点、发布会重点、课程精华、体育精彩瞬间、游戏高光、Vlog 精彩片段等。
- 提供了本地视频路径或可访问的视频 URL，并希望生成 `highlight.mp4`。

## 什么时候不要使用

以下任务不应使用本 Skill：

- 仅压缩视频。
- 仅转换视频格式。
- 仅提取音频。
- 仅添加字幕。
- 仅做图片编辑。
- 不需要大模型判断高光的普通 `ffmpeg` 操作。

## 必需输入

Agent 必须从用户请求中提取：

- `input_video`：原始视频的本地路径。
- `instruction`：用户的剪辑目标，保留用户原始意图。
- `target_duration`：目标输出时长，单位秒；如果用户没有指定，默认 `30` 秒。
- `output_dir`：输出目录；如果用户没有指定，默认 `outputs`。

## 可选输入

- `llm_backend`：`ark` 或 `mock`。如果不传，则读取 `config/default.yaml`。
- `llm_video_url`：提供给大模型理解视频的公开 URL 或签名 URL。如果用户提供，应直接传给 `run_skill.py`。
- `config`：配置文件路径。如果不传，则使用 Skill 内置的 `config/default.yaml`。
- `target_duration`：如果用户没有明确说明时长，使用默认 `30` 秒。

## 参数抽取规则

用户说：

> 把 data/input/demo.mp4 剪成 15 秒高光，突出商品外观和核心卖点

应抽取为：

- `input_video = data/input/demo.mp4`
- `instruction = 突出商品外观和核心卖点`
- `target_duration = 15`
- `output_dir = outputs`

如果用户只说“剪出高光”，但没有说明目标时长：

- 使用 `target_duration = 30`。

如果用户没有提供 `input_video`：

- 不要执行 Skill。
- 提示用户补充本地视频路径，或补充可访问的视频 URL。

## 命令模板

基础调用：

```bash
python skills/clawcut-video-highlight/scripts/run_skill.py \
  --input_video "<input_video>" \
  --instruction "<instruction>" \
  --target_duration <target_duration> \
  --output_dir "<output_dir>"
```

指定 backend：

```bash
python skills/clawcut-video-highlight/scripts/run_skill.py \
  --input_video "<input_video>" \
  --instruction "<instruction>" \
  --target_duration <target_duration> \
  --output_dir "<output_dir>" \
  --llm_backend "<ark_or_mock>"
```

使用大模型视频 URL：

```bash
python skills/clawcut-video-highlight/scripts/run_skill.py \
  --input_video "<input_video>" \
  --instruction "<instruction>" \
  --target_duration <target_duration> \
  --output_dir "<output_dir>" \
  --llm_backend ark \
  --llm_video_url "<public_video_url>"
```

使用自定义配置：

```bash
python skills/clawcut-video-highlight/scripts/run_skill.py \
  --input_video "<input_video>" \
  --instruction "<instruction>" \
  --target_duration <target_duration> \
  --output_dir "<output_dir>" \
  --config "<config_path>"
```

## 重要执行原则

- OpenClaw/Agent 不应自行生成 `final_segments`。
- OpenClaw/Agent 不应绕过 `run_skill.py` 直接调用 `ffmpeg` 做高光剪辑。
- OpenClaw/Agent 只负责抽取参数并调用 `run_skill.py`。
- 高光判断由 `run_skill.py` 内部通过 `llm_prompts.py` 和 `llm_client.py` 完成。
- 最终出片始终基于原始 `input_video` 裁剪。
- `preview.mp4` 只用于模型理解或本地调试，不作为最终出片源。
- 如果传入 `--llm_video_url`，模型会直接使用该 URL；当前版本不会自动上传 preview 到 TOS。

## 输出文件

执行成功后应查看：

- `outputs/<video_name>/videos/highlight.mp4`：最终高光视频。
- `outputs/<video_name>/reports/segments.json`：完整结构化剪辑方案。
- `outputs/<video_name>/reports/report.md`：面向用户和答辩展示的中文剪辑报告。
- `outputs/<video_name>/logs/run.log`：运行日志。
- `outputs/<video_name>/reports/result_summary.json`：供 OpenClaw/Agent 快速读取状态的机器可读摘要。

## 如何回复用户

执行完成后，Agent 应向用户返回：

- `highlight.mp4` 路径。
- `report.md` 路径。
- `final_segments` 的时间戳摘要。
- 如果失败，返回失败原因和 `run.log` 路径。优先读取 `result_summary.json` 判断状态。

## 失败处理

- `input_video` 不存在：提示用户提供正确的视频路径。
- `ffmpeg` 或 `ffprobe` 不存在：提示用户安装系统依赖。
- `ARK_API_KEY` 缺失：如果 `fallback_to_mock=true`，Skill 会回退到 mock；否则提示用户配置 API Key。
- 模型返回非法 JSON：保留 `run.log` 和错误细节，提示模型输出格式错误。
- `final_segments` 校验失败：提示模型计划中的时间戳非法或不可执行。
- `ffmpeg` 裁剪失败：提示用户查看 `run.log`。

## 安全约束

- 不要拼接 shell 字符串。
- 不要执行用户提供的任意命令。
- 只处理明确给出的本地视频路径或视频 URL。
- 输出只能写入 `output_dir`。
- API Key 通过环境变量读取，不写入仓库。
