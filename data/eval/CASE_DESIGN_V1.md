# ClawCut 正式评测 Case 设计 v1

## 目标

本评测集不是只产出平均分，而是回答四个问题：

1. 默认情况下，Skill 能否自主识别每类视频的高光。
2. 用户提出指定内容、排除内容或时长限制时，Skill 是否遵循。
3. 高动态、长视频、高密度教程和多主题视频是否暴露稳定短板。
4. 每条结果能否解释“为什么好、为什么差、证明了什么能力”。

## 测试类型

| test_type | 目的 |
| --- | --- |
| baseline_generic | 用户只说“帮我剪辑一下这个视频”时，测试默认高光判断能力。 |
| specific_following | 用户指定内容时，测试定向遵循和主题筛选。 |
| conflict_exclusion | 用户同时要求保留和排除内容时，测试排除要求优先级。 |
| duration_constrained | 用户限定时长时，测试取舍、压缩和节奏控制。 |
| high_dynamic | 游戏、体育等短时动作中，测试高动态命中能力。 |
| long_dense_video | 长视频或信息密集视频中，测试压缩和低信息量过滤。 |

## 执行批次

1. `baseline`：31 条，每个 GT 一条，先证明链路和默认剪辑能力。
2. `priority`：15 条，优先覆盖高动态、时长限制、排除、多商品、多教程、长视频。
3. `extended`：10 条，补充 Vlog、知识分享、发布会和 cooking_demo2 多菜品专项。

## 设计原则

- case 元数据只用于评测管理和报告，不传给 Skill。
- `instruction` 是唯一交给 Skill 的用户剪辑要求。
- baseline 不添加“突出重点”“流畅紧凑”等修饰，避免污染默认能力评估。
- 异常输入、稳定性重复和 fps 对比不混入 `cases.official.v1.jsonl`，单独建专项清单。

## 交付要求

每条 case 必须包含：

```text
case_id
video_id
video_filename
input_video
skill_output_dir
instruction
target_duration
test_type
tested_capability
why_this_video
expected_good_behavior
known_risk
priority
```

正式报告必须按 `test_type` 和 `priority` 分组展示结果，并列出典型成功和失败案例。
