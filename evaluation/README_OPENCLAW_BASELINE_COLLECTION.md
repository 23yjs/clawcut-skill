# OpenClaw 基线采集工具

本工具用于批量收集 ClawCut 的真实基线剪辑产物。它只做调度和记录，不做自动评分，不改剪辑算法，不改 Ark Prompt，不改 ffmpeg 逻辑。

## 为什么不能直接调用单视频入口

正式采集要求验证完整链路：

```text
batch_dispatch_openclaw_baseline.py
-> openclaw agent
-> /skill clawcut-video-highlight
-> OpenClaw Agent 按 Skill 调用单视频入口
-> Ark 分析视频
-> 生成 highlight.mp4
```

因此批量脚本不能直接运行 Skill 主入口，也不能直接调用 ffmpeg。脚本只负责把每条 case 变成一条 OpenClaw message，并调用 `openclaw agent`。

## 为什么显式写 /skill

每条 message 都以：

```text
/skill clawcut-video-highlight
```

开头。这样可以减少 Agent 自行判断应使用哪个 Skill 的波动。后面的 `[CLAWCUT_BASELINE_COLLECTION_V1]` 协议块只用于稳定传递调度参数，真正传给剪辑 Skill 的 `--instruction` 仍然只能是：

```text
帮我剪辑一下这个视频
```

## 为什么每条 case 使用独立 session

每条视频使用独立 session key：

```text
clawcut-baseline-<case_id>-<run_id>
```

这样可以避免上一条视频的上下文污染下一条视频。每次 attempt 也使用独立目录：

```text
outputs/openclaw_collection_v1/<video_id>/<case_id>/run_NN/
```

## 为什么不能猜最终产物路径

Skill 会在传入的 `output_dir` 后继续追加视频名。批量脚本执行结束后必须递归查找真实文件：

```text
**/reports/result_summary.json
**/videos/highlight.mp4
```

同一个 `run_NN/` 中找到多个结果文件会标记为 `ambiguous_output`。

## Dry Run

在 OpenClaw 容器可见的仓库目录中运行：

```bash
python3 evaluation/batch_dispatch_openclaw_baseline.py \
  --cases data/eval/baseline_openclaw_cases.v1.jsonl \
  --output-root /home/node/.openclaw/workspace/outputs/openclaw_collection_v1 \
  --agent main \
  --dry-run
```

dry-run 不调用 Ark，不生成成片，只检查 case 清单、输入文件、URL、OpenClaw 命令和 message 生成结果。输出：

```text
/home/node/.openclaw/workspace/outputs/openclaw_collection_v1/dry_run_report.json
/home/node/.openclaw/workspace/outputs/openclaw_collection_v1/dry_run_report.md
```

## 正式执行

```bash
python3 evaluation/batch_dispatch_openclaw_baseline.py \
  --cases data/eval/baseline_openclaw_cases.v1.jsonl \
  --output-root /home/node/.openclaw/workspace/outputs/openclaw_collection_v1 \
  --agent main \
  --resume \
  --max-attempts 2
```

脚本严格串行执行，每完成一个 attempt 立即更新：

```text
batch_progress.json
batch_results.csv
batch_results.jsonl
batch_dispatch.log
```

## 断点续跑

`--resume` 会读取既有 `attempt_manifest.json`：

- `official_success`：跳过。
- `diagnostic_skill_fallback`：继续用下一个 `run_NN` 重试，直到达到 `--max-attempts`。
- `diagnostic_openclaw_fallback`：继续用下一个 `run_NN` 重试。
- `failed`：继续用下一个 `run_NN` 重试。
- `ambiguous_output`：继续用下一个 `run_NN` 重试。

脚本不会覆盖已有 `run_NN/` 目录。

## 结果状态

- `official_success`：OpenClaw Gateway 正常完成，Skill 使用 Ark，未触发 fallback，且产物存在。
- `diagnostic_skill_fallback`：OpenClaw 正常完成，但 Skill 内部发生 fallback，产物只适合作诊断。
- `diagnostic_openclaw_fallback`：OpenClaw 从 Gateway fallback 到 embedded 或出现 Gateway 超时。
- `ambiguous_output`：同一 attempt 下发现多个结果文件或多个成片。
- `failed`：其他失败情况。

## 单独重跑某一个 case

例如只重跑 `cooking_demo1`：

```bash
python3 evaluation/batch_dispatch_openclaw_baseline.py \
  --cases data/eval/baseline_openclaw_cases.v1.jsonl \
  --output-root /home/node/.openclaw/workspace/outputs/openclaw_collection_v1 \
  --agent main \
  --resume \
  --max-attempts 2 \
  --only-case generic__cooking_demo1
```

如果已有 `run_01` 是 fallback，脚本会写入 `run_02`，不会覆盖旧结果。

## 当前清单注意事项

当前基线清单由非空 `data/eval/*.json` 生成，每份 GT 一条通用基线。`cooking_demo2.json` 已入库并包含在清单中。

以下历史命名暂时兼容，不在本轮重命名：

- `knowledgr-share-demo5.json`
- `product_lanuch_demo1.json`
- `product_lanuch_demo2.json`
