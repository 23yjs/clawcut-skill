# ClawCut 完整评测体系 v1

## 评测分层

ClawCut 评测体系分为五层：

1. 剪辑效果：`selection_score_v1` 判断选了什么内容。
2. 技术质量：`technical_quality` 检查解码、时长、黑屏、冻结、静音和重复片段。
3. 调用链路：OpenClaw baseline 批跑验证 `openclaw agent -> /skill -> run_skill.py`。
4. 稳定性与成本：重复运行后统计成功率、fallback、耗时、token 和估算成本。
5. 人类解释报告：把底层证据翻译成非专业人员可读结论。

## 标准执行顺序

1. 先用 `data/eval/baseline_openclaw_cases.v1.jsonl` 通过 OpenClaw 收集 baseline 成片。
2. 对 `data/eval/cases.official.v1.jsonl` 做产物预检，确认哪些 case 已经具备正式评测条件。
3. 用 `data/eval/cases.official.v1.jsonl` 对已有成片进行正式效果评测；该文件同时包含 case 设计字段和容器内执行字段。
4. 批量评测完成后自动生成 `report.html`、`summary.md`、`technical_appendix.html` 和单 case 页面。
5. 单独运行异常评测、稳定性汇总和 fps 敏感性专项。

## 关键命令

```bash
python evaluation/validate_official_cases.py \
  --cases data/eval/cases.official.v1.jsonl \
  --output-dir eval_outputs/official_v1_readiness \
  --require-ready

python evaluation/run_batch_eval.py \
  --cases data/eval/cases.official.v1.jsonl \
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
```

`cases.official.v1.jsonl` 中的 `input_video` 和 `skill_output_dir` 默认面向 OpenClaw 容器路径：

```text
/home/node/.openclaw/workspace/data/input/<video_filename>
/home/node/.openclaw/workspace/outputs/openclaw_collection_v1/<video_id>/<case_id>/run_01
```

因此正式批量评测应在容器或同等路径映射环境中运行。

## 不混分原则

- 异常场景只评价系统行为，不进入 `selection_score_v1`。
- mock fallback 和 OpenClaw embedded fallback 只作为诊断样本。
- fps 对比只用于判断是否需要高动态专项策略，不改默认 `video_fps=1`。
- DOVER 和 Judge 不替代内容选择评分。

## 交付报告必须回答的问题

- 哪些视频剪得好，为什么好。
- 哪些视频剪得差，差在哪里。
- 哪些失败来自输入、调用链路、模型输出、ffmpeg 或技术质量。
- 哪些视频成本高、耗时长、fallback 多或重复运行波动大。
- 高动态视频是否证明需要更高 fps 或局部二次分析。
