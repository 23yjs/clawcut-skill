# clawcut-skill

`clawcut-skill` 是一个面向 OpenClaw 的视频高光剪辑 Skill。它支持 `ark` 和 `mock` 两种 LLM backend：大模型负责理解视频、定义高光、语义分块、片段评分和剪辑规划；`ffmpeg` 始终根据最终时间戳从原始 `input_video` 裁剪和拼接。

## 架构说明

```text
用户任务
  -> OpenClaw 根据 SKILL.md 抽取参数
  -> run_skill.py 执行固定流程
  -> ffprobe 探测原始视频
  -> ffmpeg 生成低码率连续 preview
  -> LLM 生成结构化剪辑计划
  -> plan_validator 校验 final_segments
  -> ffmpeg 基于原始 input_video 裁剪拼接
  -> 输出 highlight.mp4 / report.md / result_summary.json
```

`SKILL.md` 是 OpenClaw/Agent 的调用协议，不是模型 Prompt。真正的大模型决策 Prompt 在 `skills/clawcut-video-highlight/scripts/llm_prompts.py` 中。

## 配置

默认配置在 `skills/clawcut-video-highlight/config/default.yaml`。核心项包括：

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
```

设置 Ark API Key：

```bash
export ARK_API_KEY=xxx
```

`fallback_to_mock: true` 时，如果 Ark 配置缺失或调用失败，项目会自动回退到 `mock`，方便本地跑通。

## 本地运行

请先自行把测试视频放到 `data/input/`，例如 `data/input/demo.mp4`。系统依赖需要本机已安装 `ffmpeg` 和 `ffprobe`。

### 目标时长策略

`--target_duration` 是可选参数：

- 用户明确指定时长时，传入 `--target_duration`，Skill 会严格围绕该时长生成 `final_segments`。
- 用户没有指定时长时，不传 `--target_duration`，程序会在 `ffprobe` 后根据原视频时长自动推荐：

```text
recommended_duration = min(video_duration, min(max(video_duration × 0.15, 15), 60))
```

例如 30 秒视频推荐 15 秒，120 秒视频推荐 18 秒，300 秒视频推荐 45 秒，600 秒以上视频推荐 60 秒。若原视频短于 15 秒，推荐时长不会超过视频本身。

用户指定时长：

```bash
python skills/clawcut-video-highlight/scripts/run_skill.py \
  --input_video data/input/demo.mp4 \
  --instruction "剪出商品外观和核心卖点高光" \
  --target_duration 30 \
  --output_dir outputs \
  --llm_backend mock
```

用户不指定时长：

```bash
python skills/clawcut-video-highlight/scripts/run_skill.py \
  --input_video data/input/demo.mp4 \
  --instruction "剪出这个视频的高光时刻" \
  --output_dir outputs \
  --llm_backend mock
```

诊断性自由时长实验：

```bash
python skills/clawcut-video-highlight/scripts/run_skill.py \
  --input_video data/input/demo.mp4 \
  --instruction "剪出这个视频的高光时刻" \
  --output_dir outputs \
  --llm_backend ark \
  --llm_video_url "https://your-public-video-url/demo.mp4" \
  --duration_policy_mode llm_free
```

`llm_free` 仅用于诊断实验：用户未指定 `--target_duration` 时不预设 15%/15-60 秒预算，让视频模型自行决定成片长度。正式默认策略仍是 `bounded_auto`。

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

如果不传 `--output_dir`，默认写入 `outputs`。

## OpenClaw 调用

OpenClaw/Agent 应根据 `skills/clawcut-video-highlight/SKILL.md` 做三件事：

1. 从用户自然语言中抽取 `input_video`、`instruction`、`output_dir`，如果用户明确说明时长，再抽取 `target_duration`。
2. 调用 `skills/clawcut-video-highlight/scripts/run_skill.py`。
3. 读取 `outputs/<video_name>/reports/result_summary.json` 判断成功或失败，并向用户返回结果。

OpenClaw/Agent 不应自行生成 `final_segments`，也不应绕过 `run_skill.py` 直接调用 `ffmpeg` 做高光剪辑。

## 输出说明

运行时会按输入视频文件名自动创建子目录。例如输入 `data/input/ecom_cup_demo.MP4`，实际输出目录是：

```text
outputs/ecom_cup_demo/
```

成功输出：

- `videos/highlight.mp4`：最终高光视频。
- `videos/preview.mp4`：低码率连续预览视频，与原视频保持相同时间轴。
- `reports/segments.json`：完整结构化剪辑方案。
- `reports/report.md`：面向用户和答辩展示的中文剪辑报告。
- `reports/result_summary.json`：供 OpenClaw/Agent 快速读取的机器可读状态摘要。
- `logs/run.log`：完整运行日志。

`segments.json`、`report.md` 和 `result_summary.json` 会包含 `duration_policy`，记录用户是否指定时长、系统推荐时长、模型选择的 `selected_target_duration` 和允许范围。

当候选高光多于目标时长容量时，Skill 会输出 `excluded_highlights`，说明哪些候选高光因为时长限制、重复内容或优先级较低没有进入最终剪辑。这些片段不会被 `ffmpeg` 裁剪，只用于解释。

失败时也会尽量写入 `reports/result_summary.json`，其中包含 `status: failed`、错误类型、错误信息、日志路径和已生成的部分输出。

## 自动评测与 selection_score_v1

评测模块支持 Ark Instruction Resolver 自动单条评测：

```bash
python evaluation/run_eval.py \
  --input_video data/input/ecom_cup_demo1.MP4 \
  --instruction "剪出这个视频的高光时刻" \
  --skill_output_dir outputs/ecom_cup_demo1 \
  --gt_dir data/eval \
  --output_dir eval_outputs/auto_generic
```

`selection_score_v1` 是 0-100 分的选段质量分，只评价：

- 是否选择了正确内容；
- 是否覆盖用户关心内容；
- 是否混入无关或明确禁止内容；
- 是否遵循目标时长。

它不评价最终成片审美、转场、口播截断、音画同步或节奏。

`llm_free` 输出只进入 `diagnostic_only`，不产生正式 `selection_score_v1`。正式 A/B 实验建议先人工检查 `generated_case.json`，再用 `--generated_case_json` 复用同一评分标准。

## 视频输入策略

- 如果用户提供 `--llm_video_url`，模型优先使用该 URL。
- 当前版本不自动上传 preview 到 TOS。
- 如果没有提供 URL，则使用本地 `preview.mp4` 的 data URL 作为模型输入，前提是模型接口支持。
- `preview.mp4` 是低码率连续视频，与原视频保持相同时间轴。
- 最终 `highlight.mp4` 始终基于 `input_video` 原始视频裁剪生成。

## 当前限制

- 当前不自动上传 preview 到 TOS。
- 如果需要 Ark 模型读取 URL，需要用户提供可访问视频 URL。
- preview 可作为本地 data URL 输入，但是否可用取决于模型接口支持情况。
- `video_fps` 当前默认 `1`。
- 对体育、游戏、动作类快速视频，后续可加入更高 fps 或局部二次分析。
- 当前不引入 ASR、抽帧、PySceneDetect、TOS SDK。

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
