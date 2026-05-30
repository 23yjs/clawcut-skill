# ClawCut V4 Mock 评测框架

本目录提供 ClawCut V4 第一版评测链路：语义片段标注、mock backend 批量运行、指标计算、结果汇总和报告生成。

第一版目标不是优化剪辑效果，也不接真实 LLM-as-a-Judge，而是先把自动评测框架跑通。

## 1. 数据文件说明

- `data/eval/annotations.example.jsonl`：视频级语义片段标注。
- `data/eval/cases.example.jsonl`：评测 case，每行描述一条用户指令及其评测规则。

`annotation_coverage=uncovered` 的样本只进入人工案例分析，不纳入自动平均分。

## 2. 快速运行

```bash
python evaluation/run_eval.py \
  --cases data/eval/cases.example.jsonl \
  --annotations data/eval/annotations.example.jsonl \
  --output_dir eval_outputs/mock_v1 \
  --backend mock \
  --run_skill
```

## 3. 独立 GT 文件模式

新的推荐模式是每个视频对应一个独立 JSON 文件：

```text
data/input/ecom_cup_demo1.MP4
→ data/eval/ecom_cup_demo1.json
```

运行：

```bash
python evaluation/run_eval.py \
  --cases data/eval/cases.example.jsonl \
  --gt_dir data/eval \
  --output_dir eval_outputs/mock_gt_dir_v1 \
  --backend mock \
  --run_skill
```

说明：

- `--gt_dir` 是新的推荐模式，会按 `video_id` 读取 `data/eval/<video_id>.json`。
- `--annotations` 是旧版兼容模式，继续支持 JSONL。
- 后续会逐步减少人工维护 `cases.jsonl` 的负担，但本次不处理。

## 4. 整数 GT 时间戳与边界容忍

人工 GT 使用整数秒标注：

```json
{
  "segment_id": "seg_001",
  "start": 9,
  "end": 15
}
```

Skill 输出允许使用浮点秒。评测匹配时，GT 左右边界默认各放宽 1 秒：

```text
原始 GT：9-15 秒
容忍区间：8-16 秒
```

容忍区间只用于判断预测片段是否命中 GT，不用于替代原始 IoU。

generic 默认高光评测输出：

```text
default_highlight_precision
default_highlight_recall
default_highlight_f1
avg_default_highlight_iou
```

其中 `Precision / Recall / F1` 是面向用户展示的主要语义指标；`avg_default_highlight_iou` 保留为底层重叠质量参考。

## 5. Ark Instruction Resolver 自动单条评测

新的自动单条评测模式不再要求人工先维护一条 `cases.jsonl` case。人工只需要维护：

```text
data/eval/<video_stem>.json
```

评测前先运行 ClawCut Skill，生成：

```text
outputs/<video_stem>/reports/segments.json
```

然后运行：

```bash
python evaluation/run_eval.py \
  --input_video data/input/ecom_cup_demo1.MP4 \
  --instruction "只剪紫色大杯的外观和装饰细节，不要片尾账号信息" \
  --target_duration 20 \
  --skill_output_dir outputs/ecom_cup_demo1 \
  --gt_dir data/eval \
  --output_dir eval_outputs/auto_ark_specific
```

该模式固定使用真实 Ark Instruction Resolver。请先设置：

```bash
export ARK_API_KEY="你的真实 API Key"
```

默认情况下，Resolver 会复用 `skills/clawcut-video-highlight/config/default.yaml` 中的 Ark 配置：

```yaml
llm:
  model: ep-20260526173832-2vrr2
  api_key_env: ARK_API_KEY
  base_url: https://ark.cn-beijing.volces.com/api/v3
```

如果临时需要覆盖，也可以使用 `--resolver_model`、`--resolver_base_url`、`--resolver_api_key_env` 和 `--resolver_timeout_seconds`。

Resolver 的职责是把用户自然语言指令解析为 GT 片段 ID：

```text
instruction + GT.video_summary + GT.semantic_segments
→ relevant_segment_ids / forbidden_segment_ids
```

重要边界：

- Resolver 只读取用户指令和 GT 文本。
- Resolver 不读取原始视频、视频 URL 或 preview。
- Resolver 不读取 `final_segments`、`segments.json` 中的片段标题和 reason。
- Resolver 不读取 `highlight.mp4`。
- Resolver 失败、JSON 非法或返回不存在的 `segment_id` 时，不回退 mock，评测状态为 `resolver_failed`。
- `generated_case.json` 会冻结本次评分标准，后续重复实验可以复用这个标准。

自动单条模式会输出：

- `resolver_request.json`：发给 Resolver 的请求内容，不包含 Skill 答案。
- `resolver_response.json`：Resolver 返回的结构化结果。
- `resolver_metadata.json`：模型、prompt 版本、耗时和 token 用量。
- `generated_case.json`：由 Resolver 生成的单条评测 case。
- `evaluation_result.json`：确定性评分结果。
- `eval_report.md`：中文评测报告。

当前评分路径：

- `generic`：使用默认高光 `Precision / Recall / F1`。
- `specific`：使用 `relevant_segment_ids` 的 Precision / Recall / F1。
- `conflict`：同时检查 `relevant_segment_ids` 和 `forbidden_segment_ids`。
- `partial / unresolved`：不强行打分，进入人工复核。

## 6. dry run

只打印将要执行的 `run_skill.py` 命令，不实际剪辑视频。

```bash
python evaluation/run_eval.py \
  --cases data/eval/cases.example.jsonl \
  --gt_dir data/eval \
  --output_dir eval_outputs/mock_v1 \
  --backend mock \
  --run_skill \
  --dry_run
```

## 7. 只评分已有输出

如果已经有 `eval_outputs/mock_v1/runs/<case_id>/...` 产物，可以只重新评分：

```bash
python evaluation/run_eval.py \
  --cases data/eval/cases.example.jsonl \
  --gt_dir data/eval \
  --output_dir eval_outputs/mock_v1_score_only \
  --skill_output_root eval_outputs/mock_v1/runs \
  --score_only
```

## 8. 评测路径说明

- `generic`：主要使用 `default_highlight_score`，同时检查默认应避免内容。
- `specific`：主要使用 `must_cover_tags` / `must_avoid_tags`。
- `conflict`：用户明确禁止的内容优先级高于默认高光。
- `partial`：结合 tags 和 `description_mock_judge`。
- `uncovered`：`manual_only`，不纳入自动平均分。

## 输出说明

评测输出目录包含：

- `results.csv`：所有 case 的表格结果。
- `eval_report.md`：Markdown 汇总报告。
- `cases/*.result.json`：每个 case 的完整指标明细。
- `runs/<case_id>/`：调用 `run_skill.py` 产生的剪辑输出。

## 当前限制

- 第一版默认使用 mock backend。
- `description_mock_judge` 是启发式 mock，不是真实 LLM Judge。
- 不新增 ASR、抽帧、场景检测或 TOS 上传能力。
