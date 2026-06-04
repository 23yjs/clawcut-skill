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
instruction + 可选 target_duration + GT.video_summary + GT.semantic_segments
→ relevant_segment_ids / forbidden_segment_ids / required_highlight_segment_ids / allowed_context_segment_ids / duration_constraint
```

`duration_constraint` 只负责将自然语言时长要求转换为统一的可接受时长区间：

```text
[min_seconds, max_seconds]
```

`duration_constraint` 会接管 `duration_score` 的时长合规计算；当用户未指定可量化时长时，评测会与 Skill 默认目标时长解耦。`selection_score_v1` 和 `final_score_v2` 的权重公式保持不变。

常见示例：

```text
30 秒        → [27, 33]
30 秒左右    → [24, 36]
30 秒以内    → [0, 30]
至少 30 秒   → [30, null]
30 到 45 秒  → [30, 45]
未指定时长    → [null, null]
```

时长评分规则：

- `status=resolved` 时，成片时长落在 `[min_seconds, max_seconds]` 区间内，`duration_score=1.0`。
- 低于下限或超过上限时，按照偏离最近边界的比例扣分。
- `status=not_specified` 时，不评价成片时长是否符合要求，`duration_score=1.0`，`duration_score_method=not_applicable`。
- `status=unresolved` 时，不擅自量化，`duration_score=null`，进入 `manual_review_required`。

用户没有提出可量化时长要求时：

- 不使用 Skill 自己的默认 `selected_target_duration` 作为评分分母。
- `recommended_duration`、`selected_target_duration` 和 `compression_ratio` 仍保留在报告中，仅作为运行记录。
- generic 使用 GT 中 4-5 分真正默认高光的加权价值作为 `generic_value_score` 分母。
- specific / conflict 使用全部 relevant GT 时长作为 Coverage 分母。

示例：

```text
控制在 30 秒以内
→ [0, 30]
→ 实际 20 秒：duration_score=1.0
→ 实际 45 秒：duration_score=0.5

剪成 30 秒左右
→ [24, 36]
→ 实际 30 秒：duration_score=1.0
→ 实际 45 秒：duration_score=0.75
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

- `generic`：使用默认高光价值、默认应避免内容和时长控制计算 `selection_score_v1`。
- `specific`：使用 `relevant_segment_ids` 的时间级 Precision / Coverage / F1。
- `conflict`：同时检查 `relevant_segment_ids` 和 `forbidden_segment_ids`。
- `partial / unresolved`：不强行打分，进入人工复核。
- `llm_free`：用户未指定可量化时长时的默认策略；`duration_score=1.0` 且不使用 Skill 默认目标时长作为评分分母，仍输出正式 `selection_score_v1`。

## 6. 完整评测闭环与 final_score_v2

新版自动评测拆成四层：

```text
Layer 0：artifact_validation
Layer 1：selection_score_v1
Layer 2：technical_quality
Layer 3：editing_experience_score_v1
```

`artifact_validation` 会读取 `reports/result_summary.json`、`reports/segments.json`、`videos/highlight.mp4` 和 `logs/run.log`，确认本次产物与当前 `input_video / instruction / target_duration` 一致。正式评分要求：

```text
skill_backend_used = ark
fallback_used = false
```

如果 Skill 回退到 mock，评测只进入 `diagnostic_only`，不会输出正式总分。

`technical_quality` 使用 `ffprobe` 和 `ffmpeg` 检查成片能否正常播放，属于硬性技术检查：

- rendered_duration 与计划时长偏差；
- 是否有视频流；
- 原视频有音频时成片是否保留音频；
- 是否能正常解码；
- 黑屏比例；
- 冻结画面比例；
- 持续静音比例；
- 源时间轴重复选择比例；
- compression_ratio。

`technical_quality` 是硬门槛和 warning 层，不做复杂加权。FFmpeg 失败属于无效成片，不是低分成片。

DOVER 是可选的感知视频质量诊断工具，用于评价画面本身的技术质量和视觉美感。它不判断是否剪到了真正高光，也不判断是否符合用户指令。默认关闭：

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

如果没有安装 DOVER，默认只记录 `dover_status=unavailable`，不会阻塞其他评测。只有传入 `--require_dover` 时，DOVER 不可用才会使本次评测失败。安装说明见 [docs/dover_setup.md](../docs/dover_setup.md)。

`editing_experience_score_v1` 是 Ark 剪辑体验 Judge 分数。为兼容旧结果，系统继续输出 `aesthetic_score_v1`，但它只是 deprecated alias。Judge 只允许读取：

- 最终 `highlight.mp4` 的 `--judge_video_url`；
- 用户 instruction；
- video_type；
- 可选 target_duration；
- 可选 rendered_duration。

Judge 严禁读取 GT、semantic_segments、Resolver 输出、`final_segments`、Skill reason、selection_score 或原视频 URL。Judge 不判断高光命中率，也不重复判断画质；画面清晰度、压缩伪影由 DOVER 负责，黑屏、冻结、音频流缺失由 FFmpeg 负责。

Judge 五项评分维度均为 0-5：

- `clip_boundary_completeness`
- `transition_coherence`
- `pacing_and_conciseness`
- `audio_visual_continuity`
- `standalone_watchability`

Python 端计算：

```text
editing_experience_score_v1 = 20 × 五项平均分
aesthetic_score_v1 = editing_experience_score_v1  # 兼容别名
```

Judge 还会输出标准化 `issues` 列表，用于记录动作截断、口播截断、突兀转场、冗余、节奏问题、音频切断、缺少上下文或独立观看性不足等剪辑体验问题。常见 `issue_type` 包括 `action_truncation`、`speech_truncation`、`abrupt_transition`、`redundancy`、`audio_cut_abrupt` 和 `not_standalone_watchable`。`issues` 不参与 `editing_experience_score_v1`，只用于失败原因统计、人工校准和典型案例分析；多次 Judge 汇总会输出 `judge_issue_counts`。它不记录高光遗漏、指令不满足、画质、黑屏、冻结或音频流缺失。

最终综合分：

```text
final_score_v2 = 0.70 × selection_score_v1 + 0.30 × aesthetic_score_v1
```

如果没有传 `--judge_video_url`：

```text
evaluation_status = selection_scored_aesthetic_pending
selection_score_v1 = 保留
aesthetic_score_v1 = null
final_score_v2 = null
```

注意：`--judge_video_url` 必须是最终 `videos/highlight.mp4` 的可访问 URL，不是原视频 URL。也可以启用 `--auto_upload_judge_video` 自动把本地 `highlight.mp4` 上传到 TOS，并将生成的 GET 预签名 URL 仅用于 Ark Judge。

默认 TOS 对象路径：

```text
output/<video_id>/instruction-<instruction_hash>/<run_id>/highlight.mp4
```

`instruction_hash` 由 `video_id`、用户指令和 `target_duration` 共同计算；`run_id` 来自本次评测输出目录名。因此同一视频在不同指令、不同目标时长或不同评测 run 下不会覆盖旧文件。评测目录会额外输出 `tos_upload.json`，只保存去除 query string 的 URL、完整签名 URL 的 sha256、bucket 和 object key，不保存 AK/SK 或签名参数。

自动上传示例：

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

## 7. selection_score_v1 选段质量总分

`selection_score_v1` 是 0-100 分的确定性选段质量分，只评价“选了哪些内容”，不评价成片审美、镜头衔接、音画同步、口播截断或剪辑节奏。

正式输出字段：

```text
evaluation_status = scored
evaluation_scope = official
score_version = selection_score_v1
selection_score_v1 = 0-100
final_score_v2 = null 或综合分
```

Generic 指令使用默认高光价值评分：

```text
generic_core_score
= harmonic_mean(
  generic_value_score,
  acceptable_precision
)

selection_score_v1
= 100
× generic_core_score
× default_avoid_compliance_score
× duration_score
```

Generic 时间区间级指标还会输出：

```text
required_highlight_segment_ids
allowed_context_segment_ids
acceptable_overlap_duration
acceptable_precision
default_highlight_duration
default_highlight_precision
generic_target_source
```

其中：

```text
acceptable_precision
= 输出中落入 required_highlight 或 allowed_context 区间的时长 / 输出总时长

default_highlight_precision
= 输出中落入默认高光区间的时长 / 输出总时长
```

generic 模式不再只依赖固定 4-5 分阈值决定最终 Precision。Resolver 会根据用户指令和人工 GT 描述区分：

```text
required_highlight_segment_ids：必须覆盖的核心高光
allowed_context_segment_ids：允许保留的合理上下文
```

`generic_value_score` 用于评价是否覆盖 required 核心高光的加权价值；有明确时长预算时，只在 required 集合内计算预算内理论最优价值。

当用户没有明确指定剪辑时长时，generic 不使用 Skill 默认目标时长，输出 `generic_value_mode=full_gt_required`，使用全部 required 核心高光加权价值作为召回分母。

`acceptable_precision` 用于评价输出中可接受内容的占比。合理上下文不会被错误惩罚，普通低价值内容和 avoid 内容仍会降低该指标。

`generic_core_score` 使用 `generic_value_score` 和 `acceptable_precision` 的调和平均。任何一项明显偏低，generic 内容选择分都会下降。

`default_highlight_precision` 继续保留为严格诊断指标，定义为固定阈值高光区间：

```text
default_highlight_score >= 4
且 avoid_by_default = false
```

DOVER 仍然只作为感知质量诊断。Judge 五维体验分和 `final_score_v2` 公式没有修改。

Specific / conflict 指令使用 guided 评分。Resolver 会输出：

```text
selection_scope:
  preferential  # 重点保留目标内容，允许少量上下文
  exclusive     # 只剪目标内容，原则上不允许混入非目标内容
```

specific / conflict 模式允许 Resolver 标记少量 `allowed_context_segment_ids`。Coverage 仍只计算 `relevant_segment_ids`，Precision 使用 `relevant_segment_ids + allowed_context_segment_ids` 作为可接受内容，`forbidden_segment_ids` 继续单独计算惩罚。

`allowed_context_segment_ids` 只能用于保证动作完整、语义连贯、因果清楚或结果反馈完整，不能作为普通低价值内容的免责桶。

`preferential`：

```text
guided_core_score = 0.70 × relevant_duration_coverage + 0.30 × acceptable_precision
```

当用户没有明确指定剪辑时长时，guided coverage 使用全部 relevant GT 时长作为分母，输出 `coverage_mode=full_gt`；有明确时长预算时仍使用 `coverage_mode=budgeted`。

`exclusive`：

```text
guided_core_score = F1(acceptable_precision, relevant_duration_coverage)
```

最终：

```text
selection_score_v1
= 100
× guided_core_score
× forbidden_compliance_score
× duration_score
```

时间区间会先做并集。例如 `0-10` 和 `8-15` 只按 `0-15` 计算为 15 秒，不会重复计时。

## 8. frozen generated_case 复用

Resolver 输出存在轻微波动。正式 A/B 实验应先人工检查 `generated_case.json`，然后复用冻结后的评分标准：

```bash
python evaluation/run_eval.py \
  --input_video data/input/ecom_cup_demo1.MP4 \
  --instruction "剪出这个视频的高光时刻" \
  --skill_output_dir outputs/ecom_cup_demo1 \
  --gt_dir data/eval \
  --generated_case_json eval_outputs/auto_ark_generic/generated_case.json \
  --output_dir eval_outputs/auto_ark_generic_frozen
```

传入 `--generated_case_json` 时不会调用 Ark Resolver。程序会校验 `video_id`、`instruction`、`target_duration` 和所有 `segment_id`，任何不一致都会直接报错。

每个评测目录都会复制本次使用的 `generated_case.json`，并写入 `resolver_metadata.json`，保证单个目录可以独立复现。

## 9. run_manifest 可复现清单

自动评测会输出 `run_manifest.json`，记录：

- git commit；
- input_video / GT / generated_case / segments_json / highlight.mp4 的 sha256；
- instruction、target_duration、duration_policy_mode；
- Skill / Resolver / Aesthetic Judge 的 prompt version 和模型；
- backend/fallback 状态；
- Judge repeat 次数；
- `judge_video_url_sha256`。

为避免泄露签名 URL，manifest 不保存 TOS query string，只保存去除 query 的可读 URL 和完整 URL 的 sha256。

## 10. 批量评测

批量输入 JSONL 示例：

```json
{"case_id":"case_001","input_video":"data/input/ecom_cup_demo1.MP4","instruction":"剪出这个视频的高光时刻","skill_output_dir":"outputs/ecom_cup_demo1","judge_video_url":"https://example.com/highlight.mp4"}
```

运行：

```bash
python evaluation/run_batch_eval.py \
  --cases data/eval/batch_cases.jsonl \
  --gt_dir data/eval \
  --output_dir eval_outputs/batch_v1
```

正式 official case 建议先做产物预检，再只评分已经具备有效 Ark 成片的 ready 子集：

```bash
python evaluation/validate_official_cases.py \
  --cases data/eval/cases.official.v1.jsonl \
  --output-dir eval_outputs/official_v1_readiness

python evaluation/run_batch_eval.py \
  --cases eval_outputs/official_v1_readiness/official_ready_cases.jsonl \
  --gt_dir data/eval \
  --output_dir eval_outputs/official_v1
```

`official_diagnostic_cases.jsonl` 会保留 fallback 样本，用于单独分析调用链路或 Skill fallback，不进入正式剪辑效果平均分。

异常场景评测支持两种模式：不传 `--results-jsonl` 时只校验异常 case 清单；传入实际执行结果后，会检查每条异常用例是否得到预期错误类型、是否写入 `result_summary` 和 `run.log`、是否避免卡死、是否没有错误生成 misleading 成片、是否没有进入正式效果评分。

```bash
python evaluation/run_abnormal_eval.py \
  --cases data/eval/abnormal_cases.v1.jsonl \
  --results-jsonl eval_outputs/abnormal_v1/abnormal_results.jsonl \
  --output-dir eval_outputs/abnormal_v1
```

版本回归评测用于回答“新版本到底变好了还是退步了”。它只读取两次批量评测的 `results.csv`，不重新调用 Skill 或模型：

```bash
python evaluation/regression_report.py \
  --baseline-results eval_outputs/official_v1_baseline/results.csv \
  --candidate-results eval_outputs/official_v1_candidate/results.csv \
  --gate-config evaluation/config/regression_gate.v1.json \
  --output-dir eval_outputs/regression_v1 \
  --fail-on-regression
```

默认门禁会拦截：case 丢失、原本成功的 case 变失败、fallback 增加、技术质量退步、单 case 分数下降超过阈值、平均分下降超过阈值。

最终交付报告用于把上述分散结果聚合成训练营可提交材料：

```bash
python evaluation/final_delivery_report.py \
  --official-summary-json eval_outputs/official_v1/summary.json \
  --readiness-json eval_outputs/official_v1_readiness/official_case_readiness.json \
  --abnormal-summary-json eval_outputs/abnormal_v1/abnormal_summary.json \
  --stability-summary-json eval_outputs/stability_v1/stability_summary.json \
  --fps-summary-json eval_outputs/fps_sensitivity_v1/fps_sensitivity_summary.json \
  --regression-summary-json eval_outputs/regression_v1/regression_summary.json \
  --output-dir eval_outputs/final_delivery_v1
```

它会生成 `FINAL_EVALUATION_REPORT.md` 和 `final_delivery_report.json`，集中说明总体结论、能力覆盖、典型成功/失败案例、异常处理、稳定性/成本、高动态 fps、版本回归和缺失证据层。

输出：

```text
results.csv
summary.json
summary.md
report.html
technical_appendix.html
cases/<case_id>.html
runs/<case_id>/evaluation_result.json
runs/<case_id>/eval_report.md
runs/<case_id>/run_manifest.json
```

`report.html` 和 `summary.md` 面向非专业读者，包含按测试类型/优先级的能力汇总、典型成功案例、典型失败或待优化案例。`technical_appendix.html` 保留底层字段，便于开发者追溯 selection、technical quality、Judge、token、耗时和 fallback。

单条失败不会中断整个批次。

## 11. dry run

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

## 12. 只评分已有输出

如果已经有 `eval_outputs/mock_v1/runs/<case_id>/...` 产物，可以只重新评分：

```bash
python evaluation/run_eval.py \
  --cases data/eval/cases.example.jsonl \
  --gt_dir data/eval \
  --output_dir eval_outputs/mock_v1_score_only \
  --skill_output_root eval_outputs/mock_v1/runs \
  --score_only
```

## 13. 评测路径说明

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
- 不新增 ASR、抽帧或场景检测；TOS 上传是可选能力，只在 `--auto_upload_judge_video` 开启时执行。
