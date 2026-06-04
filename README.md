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
- 用户没有指定时长时，不传 `--target_duration`，默认使用 `llm_free`：不预设固定目标时长，由模型根据高光数量、内容密度、事件完整性和冗余程度决定成片长度。
- `bounded_auto` 仍保留为可选基线模式，可通过 `--duration_policy_mode bounded_auto` 显式启用；该模式会使用 `recommended_duration = min(video_duration, min(max(video_duration × 0.15, 15), 60))`。

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

显式启用 bounded_auto 基线：

```bash
python skills/clawcut-video-highlight/scripts/run_skill.py \
  --input_video data/input/demo.mp4 \
  --instruction "剪出这个视频的高光时刻" \
  --output_dir outputs \
  --llm_backend ark \
  --llm_video_url "https://your-public-video-url/demo.mp4" \
  --duration_policy_mode bounded_auto
```

`llm_free` 是当前默认策略，不是诊断模式。评测侧在用户未指定可量化时长时会设置 `duration_score=1.0`，不使用 Skill 自己的默认目标时长作为评分分母，仍然可以输出正式 `selection_score_v1`。

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

## 自动评测、审美 Judge 与 final_score_v2

评测模块支持 Ark Instruction Resolver 自动单条评测：

```bash
python evaluation/run_eval.py \
  --input_video data/input/ecom_cup_demo1.MP4 \
  --instruction "剪出这个视频的高光时刻" \
  --skill_output_dir outputs/ecom_cup_demo1 \
  --gt_dir data/eval \
  --output_dir eval_outputs/auto_generic
```

完整评测分为四层：

- Layer 0 `artifact_validation`：检查本轮 Skill 产物是否和当前输入、指令、时长一致，并拦截 mock fallback。
- Layer 1 `selection_score_v1`：0-100 分，只评价“剪了什么”。
- Layer 2 `technical_quality`：用 ffprobe/ffmpeg 检查成片是否可解码、时长是否合理、音频是否一致、黑屏、冻结、静音和重复源区间情况。
- Layer 3 `editing_experience_score_v1`：Ark 剪辑体验 Judge，只评价最终 `highlight.mp4` 的剪辑体验。

`selection_score_v1` 只评价：

- 是否选择了正确内容；
- 是否覆盖用户关心内容；
- 是否混入无关或明确禁止内容；
- 是否遵循目标时长。

FFmpeg / ffprobe 是硬性技术检查：判断成片能否正常播放，检测黑屏、冻结、音频流、静音和时长异常。FFmpeg 失败属于无效成片，不是低分成片。

DOVER 是可选外部感知质量工具：评价画面本身的技术质量与视觉美感，仅作为辅助诊断。DOVER 不判断是否剪到了真正高光，也不判断用户指令遵循情况。DOVER 默认关闭，不加入主依赖，安装说明见 `docs/dover_setup.md`。

Ark Judge 只评价剪辑体验：片段边界完整性、转场连贯性、节奏简洁性、音画连续性和独立可看性。它不读取 GT、`final_segments`、Resolver 输出或已有分数，也不重复判断画质。为兼容旧结果，`aesthetic_score_v1` 继续输出，但它是 `editing_experience_score_v1` 的 deprecated alias。

当且仅当 artifact、technical、selection 和 aesthetic 全部通过时，输出：

```text
final_score_v2 = 0.70 × selection_score_v1 + 0.30 × aesthetic_score_v1
```

如果没有提供 `--judge_video_url`，系统会保留 `selection_score_v1`，但 `aesthetic_score_v1` 和 `final_score_v2` 为 `null`，状态为 `selection_scored_aesthetic_pending`。

`--judge_video_url` 必须指向最终上传后的 `videos/highlight.mp4`，不要传原视频 URL。也可以启用 `--auto_upload_judge_video`，评测会把本地 `highlight.mp4` 上传到 TOS 后生成 Judge 使用的临时 GET 预签名 URL。默认对象路径为：

```text
output/<video_id>/instruction-<instruction_hash>/<run_id>/highlight.mp4
```

这样同一个视频在不同用户指令或不同评测输出目录下不会互相覆盖。结果目录会写入 `tos_upload.json`，只保存去除 query string 的可读 URL 和完整签名 URL 的 sha256，不保存 AK/SK 或签名参数。

正式 A/B 实验建议先人工检查 `generated_case.json`，再用 `--generated_case_json` 复用同一评分标准。`llm_free` 输出可以正式评分；只有 Skill 回退到 mock 或 artifact validation 失败时，才会进入 `diagnostic_only` 或无效产物路径。

批量评测使用：

```bash
python evaluation/run_batch_eval.py \
  --cases data/eval/batch_cases.jsonl \
  --gt_dir data/eval \
  --output_dir eval_outputs/batch_v1
```

面向训练营答辩或非专业读者的报告使用：

```bash
python evaluation/human_readable_report.py \
  --results-csv eval_outputs/batch_v1/results.csv \
  --summary-json eval_outputs/batch_v1/summary.json \
  --output-dir eval_outputs/batch_v1
```

这会生成 `report.html`、`summary.md`、`technical_appendix.html` 和 `cases/<case_id>.html`。主报告只写人能看懂的结论，并自动列出按测试类型/优先级的能力汇总、典型成功案例、典型失败或待优化案例；技术附录保留底层证据。

专项评测入口：

```bash
python evaluation/validate_official_cases.py \
  --cases data/eval/cases.official.v1.jsonl \
  --output-dir eval_outputs/official_v1_readiness

python evaluation/run_batch_eval.py \
  --cases eval_outputs/official_v1_readiness/official_ready_cases.jsonl \
  --gt_dir data/eval \
  --output_dir eval_outputs/official_v1

python evaluation/run_abnormal_eval.py \
  --cases data/eval/abnormal_cases.v1.jsonl \
  --output-dir eval_outputs/abnormal_v1

python evaluation/stability_report.py \
  --results-jsonl outputs/openclaw_collection_v1/batch_results.jsonl \
  --cost-model evaluation/config/cost_model.yaml \
  --output-dir eval_outputs/stability_v1

python evaluation/run_fps_sensitivity_eval.py \
  --cases data/eval/high_dynamic_fps_cases.v1.jsonl \
  --results-jsonl eval_outputs/fps_sensitivity_results.jsonl \
  --output-dir eval_outputs/fps_sensitivity_v1

python evaluation/regression_report.py \
  --baseline-results eval_outputs/official_v1_baseline/results.csv \
  --candidate-results eval_outputs/official_v1_candidate/results.csv \
  --gate-config evaluation/config/regression_gate.v1.json \
  --output-dir eval_outputs/regression_v1 \
  --fail-on-regression
```

正式 case 设计见 `data/eval/CASE_DESIGN_V1.md` 和 `data/eval/cases.official.v1.jsonl`。`cases.official.v1.jsonl` 同时包含 `input_video` 和 `skill_output_dir`，默认指向 OpenClaw 容器路径；正式批量评测应在容器或等价路径映射环境中运行。异常、稳定性和 fps 对比均为专项评测，不混入 `selection_score_v1` 的正式效果分。

启用 DOVER 的单视频评测示例：

```bash
python evaluation/run_eval.py \
  --input_video data/input/sports_demo1.MP4 \
  --instruction "剪出这个视频的高光时刻" \
  --skill_output_dir outputs/sports_demo1 \
  --gt_dir data/eval \
  --output_dir eval_outputs/sports_demo1_quality_v1 \
  --enable_dover \
  --dover_repo_dir /path/to/DOVER \
  --dover_opt_path /path/to/DOVER/dover-mobile.yml \
  --dover_device cpu
```

启用 TOS 自动上传并直接运行 Ark Judge：

```bash
export TOS_ACCESS_KEY="..."
export TOS_SECRET_KEY="..."

python evaluation/run_eval.py \
  --input_video data/input/ecom_cup_demo1.MP4 \
  --instruction "剪出这个视频的高光时刻" \
  --skill_output_dir outputs/full_score_v2_ark/ecom_cup_demo1 \
  --gt_dir data/eval \
  --generated_case_json eval_outputs/ecom_cup_demo1_full_score_v2/generated_case.json \
  --output_dir eval_outputs/ecom_cup_demo1_auto_upload_score \
  --auto_upload_judge_video \
  --tos_bucket clawcut \
  --tos_region cn-beijing \
  --tos_endpoint tos-cn-beijing.volces.com \
  --tos_key_prefix output
```

## 视频输入策略

- 如果用户提供 `--llm_video_url`，模型优先使用该 URL。
- 当前版本不自动上传 preview 到 TOS。
- 如果没有提供 URL，则使用本地 `preview.mp4` 的 data URL 作为模型输入，前提是模型接口支持。
- `preview.mp4` 是低码率连续视频，与原视频保持相同时间轴。
- 最终 `highlight.mp4` 始终基于 `input_video` 原始视频裁剪生成。

## 当前限制

- 当前不自动上传 preview 到 TOS；最终 `highlight.mp4` 可通过 `--auto_upload_judge_video` 自动上传到 TOS 供 Judge 使用。
- 如果需要 Ark 剪辑模型读取 URL，需要用户提供可访问原视频或 preview URL。
- preview 可作为本地 data URL 输入，但是否可用取决于模型接口支持情况。
- `video_fps` 当前默认 `1`。
- 对体育、游戏、动作类快速视频，后续可加入更高 fps 或局部二次分析。
- 当前不引入 ASR、抽帧或 PySceneDetect；TOS Python SDK 为可选依赖，只在 `--auto_upload_judge_video` 开启时使用。

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
