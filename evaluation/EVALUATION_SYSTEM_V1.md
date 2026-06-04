# ClawCut 完整评测体系 v1

## 评测分层

ClawCut 评测体系分为六层：

1. 剪辑效果：`selection_score_v1` 判断选了什么内容。
2. 技术质量：`technical_quality` 检查解码、时长、黑屏、冻结、静音和重复片段。
3. 调用链路：OpenClaw baseline 批跑验证 `openclaw agent -> /skill -> run_skill.py`。
4. 稳定性与成本：重复运行后统计成功率、fallback、耗时、token 和估算成本。
5. 人类解释报告：把底层证据翻译成非专业人员可读结论。
6. 版本回归：比较两次 `results.csv`，判断新版本是否引入失败、fallback、技术质量或分数退步。

## 标准执行顺序

1. 先用 `data/eval/baseline_openclaw_cases.v1.jsonl` 通过 OpenClaw 收集 baseline 成片。
2. 对 `data/eval/cases.official.v1.jsonl` 做产物预检，确认哪些 case 已经具备正式评测条件，并导出 ready-only 子清单。
3. 用 `eval_outputs/official_v1_readiness/official_ready_cases.jsonl` 对已有成片进行正式效果评测；原始 official 文件继续作为完整设计清单保留。
4. 批量评测完成后自动生成 `report.html`、`summary.md`、`technical_appendix.html` 和单 case 页面；主报告必须包含能力维度汇总以及典型成功、失败和诊断样本。
5. 单独运行异常评测、稳定性汇总、fps 敏感性专项和版本回归对比。
6. 聚合生成 `FINAL_EVALUATION_REPORT.md`，作为训练营提交和答辩材料的总入口。

## 关键命令

```bash
python evaluation/evaluation_system_audit.py \
  --repo-root . \
  --output-dir eval_outputs/system_audit_precheck

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

# 如果已经执行了异常用例，追加结果文件即可做实际行为验收：
python evaluation/run_abnormal_eval.py \
  --cases data/eval/abnormal_cases.v1.jsonl \
  --results-jsonl eval_outputs/abnormal_v1/abnormal_results.jsonl \
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

python evaluation/final_delivery_report.py \
  --official-summary-json eval_outputs/official_v1/summary.json \
  --readiness-json eval_outputs/official_v1_readiness/official_case_readiness.json \
  --abnormal-summary-json eval_outputs/abnormal_v1/abnormal_summary.json \
  --stability-summary-json eval_outputs/stability_v1/stability_summary.json \
  --fps-summary-json eval_outputs/fps_sensitivity_v1/fps_sensitivity_summary.json \
  --regression-summary-json eval_outputs/regression_v1/regression_summary.json \
  --output-dir eval_outputs/final_delivery_v1

python evaluation/evaluation_system_audit.py \
  --repo-root . \
  --output-dir eval_outputs/system_audit_final \
  --require-complete
```

`cases.official.v1.jsonl` 中的 `input_video` 和 `skill_output_dir` 默认面向 OpenClaw 容器路径：

```text
/home/node/.openclaw/workspace/data/input/<video_filename>
/home/node/.openclaw/workspace/outputs/openclaw_collection_v1/<video_id>/<case_id>/run_01
```

因此正式批量评测应在容器或同等路径映射环境中运行。

如果要求 56 条 official case 全部具备产物后才允许继续，可以给预检命令增加 `--require-ready`。日常迭代中更推荐先用 `official_ready_cases.jsonl` 跑已完成子集，并把 `official_diagnostic_cases.jsonl` 留给 fallback 问题分析。

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
- 新版本相比基准版本是否出现分数下降、失败增加、fallback 增加或技术质量退步。
