# clawcut-skill

`clawcut-skill` 是一个面向 OpenClaw Skill 的第一阶段 Python 项目骨架，用于实现 LLM-centric 视频高光剪辑流程。

当前版本先跑通本地闭环：

- `mock_llm.py` 模拟大模型输出结构化 JSON。
- `ffprobe` 读取原始视频的时长、分辨率、帧率和音频信息。
- `ffmpeg` 生成连续预览视频，并根据最终时间戳裁剪、拼接高光视频。
- 第一阶段不调用真实大模型 API。

## 环境准备

安装 Python 依赖：

```bash
python -m pip install -r requirements.txt
```

如果本机没有 `ffmpeg` 和 `ffprobe`，请先用系统包管理器安装。

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

运行后会生成：

- `outputs/videos/highlight.mp4`：最终高光视频
- `outputs/videos/preview.mp4`：低码率连续预览视频
- `outputs/reports/segments.json`：结构化剪辑方案
- `outputs/reports/report.md`：中文运行报告
- `outputs/logs/run.log`：运行日志
- `outputs/work/`：中间裁剪片段

## 运行方式

```bash
python skills/clawcut-video-highlight/scripts/run_skill.py \
  --input_video data/input/ecom_cup_demo.Mp4 \
  --instruction "剪出 15 秒视频高光，突出商品外观和核心卖点" \
  --target_duration 15 \
  --output_dir outputs
```

## 语法检查

```bash
python -m compileall skills evaluation
```

## 评估剪辑方案

```bash
python evaluation/run_eval.py \
  --plan_json outputs/reports/segments.json \
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
    mock_llm.py
    plan_validator.py
    ffmpeg_editor.py
    llm_prompts.py
    utils.py
evaluation/
  metrics.py
  run_eval.py
```
