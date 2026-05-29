# ClawCut V4 示例评测数据

本目录放置第一版 V4 mock 评测使用的示例数据。示例文件只定义数据格式，不要求仓库中一定包含所有真实视频。

## 文件说明

- `annotations.example.jsonl`：视频级语义片段标注，每一行对应一个视频。
- `cases.example.jsonl`：评测 case，每一行对应一次待评测的用户指令。

## 标注约定

- `semantic_segments` 是人工语义片段，不要求逐秒细标。
- `default_highlight_score` 只在 generic / weak instruction 场景中作为主评价依据。
- `specific` 和 `conflict` 场景优先看 `must_cover_tags` / `must_avoid_tags`。
- `annotation_coverage=uncovered` 的样本只进入人工分析，不纳入自动平均分。

## 运行示例

```bash
python evaluation/run_eval.py \
  --cases data/eval/cases.example.jsonl \
  --annotations data/eval/annotations.example.jsonl \
  --output_dir eval_outputs/mock_v1 \
  --backend mock \
  --run_skill \
  --dry_run
```
