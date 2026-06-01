# DOVER 可选安装说明

DOVER 在 ClawCut 评测框架中只作为可选的画面感知质量诊断工具。它用于评价最终 `highlight.mp4` 的画面技术质量和视觉美感，不判断是否剪到了真正高光，也不判断是否符合用户指令。

当前仓库不会内置 DOVER 源码和模型权重，也不会在运行时自动下载。请在本机单独准备 DOVER 环境，然后通过 CLI 或环境变量传给评测脚本。

## 推荐准备方式

1. 在本机克隆 DOVER 官方仓库到独立目录，例如：

```bash
git clone https://github.com/VQAssessment/DOVER.git /path/to/DOVER
```

2. 按 DOVER 官方说明创建 Python 环境并安装依赖。

3. 提前下载所需模型权重，并确认 `dover.yml` 或 `dover-mobile.yml` 能在该环境中运行。

CPU 或本地快速验证优先考虑 DOVER-Mobile：

```text
/path/to/DOVER/dover-mobile.yml
```

GPU 环境可以使用完整 DOVER 配置：

```text
/path/to/DOVER/dover.yml
```

## ClawCut 运行参数

单视频评测：

```bash
python evaluation/run_eval.py \
  --input_video data/input/sports_demo1.MP4 \
  --instruction "剪出这个视频的高光时刻" \
  --skill_output_dir outputs/sports_demo1 \
  --gt_dir data/eval \
  --output_dir eval_outputs/sports_demo1_quality_v1 \
  --enable_dover \
  --dover_repo_dir /path/to/DOVER \
  --dover_python /path/to/dover-env/bin/python \
  --dover_opt_path /path/to/DOVER/dover-mobile.yml \
  --dover_device cpu
```

等价环境变量：

```bash
export DOVER_REPO_DIR=/path/to/DOVER
export DOVER_PYTHON=/path/to/dover-env/bin/python
export DOVER_OPT_PATH=/path/to/DOVER/dover-mobile.yml
export DOVER_DEVICE=cpu
export DOVER_TIMEOUT_SECONDS=300
```

## 失败策略

默认情况下，DOVER 不可用不会阻塞其他评测：

```json
{
  "dover_status": "unavailable"
}
```

只有显式传入 `--require_dover` 时，DOVER 不可用才会使本次评测进入 DOVER 失败状态。

## 输出字段

成功时评测结果会包含：

```text
dover_fused_overall_score
dover_raw_technical_score
dover_raw_visual_aesthetic_score
dover_reference_percentiles
```

字段名使用 `visual_aesthetic`，避免和 Ark 剪辑体验 Judge 的 `aesthetic_score_v1` 兼容别名混淆。
