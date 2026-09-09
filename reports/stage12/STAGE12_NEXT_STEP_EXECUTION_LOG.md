# Stage 12 后续实验执行日志

生成时间：2026-08-18

## 1. 阶段一：产物核对

已核对现有 CAM-off / SkillOpt SpreadsheetBench 结果：

```text
outputs/formal_normal/spreadsheetbench_train16_sel8_t420_seed42
```

产物状态：

| 文件 | 状态 |
|---|---|
| config.json | 存在 |
| summary.json | 存在 |
| best_skill.md | 存在 |
| history.json | 存在 |
| selection results | 存在 |
| test results | 缺失 |

关键配置：

```text
split_dir = data/spreadsheetbench_split
split_seed = 42
optimizer = gpt-5.6-sol
target = gpt-5.6-terra
exec_timeout = 420
train_size = 16
selection_size = 8
test_size = 8 configured
eval_test = false
cam_enabled = false
```

已有 selection 结果：

```text
baseline_selection_hard = 0.25
best_selection_hard = 0.50
```

## 2. 阶段一补评：CAM-off best skill valid_unseen

按计划要求：已有 skill 但缺 test 时，只补 `valid_unseen`，不重训。

补评目录：

```text
outputs/eval_only/spreadsheetbench_cam_off_best_train16_sel8_t420_seed42_valid_unseen_v2
```

补评结果：

```text
split = valid_unseen
n = 8
hard = 0.1250
soft = 0.1250
```

说明：

- 第一次补评尝试因 `eval_only.py` 读取 `best_skill.md` 使用 Windows 默认 GBK 失败；
- 已修复 `scripts/eval_only.py` 的 skill 读取和 summary 写入为 UTF-8；
- 第二次补评完成。

## 3. 阶段二：CAM-Full 并发探针 P0

探针配置：

```text
method = CAM-Full
probe = P0
workers = 1
analyst_workers = 1
train_size = 4
batch_size = 4
epochs = 1
selection_size = 4
test_size = 4
eval_test = true
target = gpt-5.6-terra
exec_timeout = 420
seed = 42
```

输出目录：

```text
outputs/stage12_probe/spreadsheetbench_terra_cam_full_P0_w1_a1
```

P0 结果：

```text
baseline_selection_hard = 0.0000
step_1_candidate_selection_hard = 0.0000
action = cam_re_evaluate
final_selection_hard = 0.0000
baseline_test_hard = 0.0000
best_test_hard = 0.2500
final_test_hard = 0.2500
wall_time = 4449s
total_calls = 26
total_tokens = 105,262
```

P0 稳定性观察：

- baseline selection 4/4 均失败；
- step candidate selection 出现 timeout / worker-stall cancellation；
- final selection 4/4 均 timeout / cancelled；
- test evaluation 可完成，但耗时极高；
- 总耗时约 74 分钟，明显超过探针预期。

P0 结论：

```text
P0 = unstable / too slow for probe matrix expansion
```

按照计划中的并发选择规则：

> P1-P3 若出现 worker stall、批量取消或 error/timeout 明显增加，正式实验回退到 P0。

但本次 P0 本身已经出现 worker stall 且耗时过高，因此不建议立即继续 P1-P3；否则更高 workers 很可能扩大 Codex CLI / Windows sandbox stall 风险。

## 4. 当前建议

建议暂停在这里，由用户确认下一步：

1. 保守路线：不跑 P1-P3，标记 SpreadsheetBench + terra 并发探针不稳定，进入补评/结果整理；
2. 强行继续：继续运行 P1，但需要接受更高 worker stall 风险和更长运行时间；
3. 工程路线：先修 Codex CLI 空代码 / shell stall 问题，再重新执行 P0/P1。

## 5. 阶段二补充：Codex CLI 空代码 / shell stall 修复与 P0 fix1

用户选择工程路线后，已完成针对 SpreadsheetBench Codex 执行链路的稳定性修复，并重新运行 P0 fix1。

修复要点：

- 将 SpreadsheetBench 目标侧 Codex 执行 prompt 调整为“直接返回 Python 函数代码”，不再要求目标模型进入 shell 读取 `task.md` / `SKILL.md`；
- 在 prompt 中直接注入完整任务和动态 skill，降低 CLI 工具调用 stall 风险；
- 增加代码规范化与 AST 级 Python 代码判定，避免把普通解释文本误当作代码；
- 针对“模型没有返回代码、而是在描述使用 shell/命令”的情况，增加一次 direct-code retry；
- `scripts/eval_only.py` 的 skill 读取与 summary 写入统一使用 UTF-8，避免 Windows GBK 读取失败。

P0 fix1 配置：

```text
method = CAM-Full
probe = P0 fix1
workers = 1
analyst_workers = 1
train_size = 4
batch_size = 4
epochs = 1
selection_size = 4
test_size = 4
eval_test = true
target = gpt-5.6-terra
exec_timeout = 420
seed = 42
```

输出目录：

```text
outputs/stage12_probe/spreadsheetbench_terra_cam_full_P0_w1_a1_fix1
```

P0 fix1 结果：

```text
baseline_selection_hard = 0.2500
best_selection_hard = 0.5000
final_selection_hard = 0.5000
baseline_test_hard = 0.2500
best_test_hard = 0.2500
final_test_hard = 0.2500
test_delta_hard = 0.0000
wall_time = 915.1s
total_calls = 30
total_tokens = 110,730
```

稳定性观察：

- baseline selection 完成 4/4，hard = 1/4；
- rollout 完成 4/4，虽然候选代码未通过任务，但未再观察到空代码 / shell stall 扩散；
- candidate selection 达到 2/4，触发 CAM re-evaluate；
- final selection 维持 2/4，并被记录为当前最佳 skill；
- test evaluation 完成 4/4，baseline 与 final 均为 1/4；
- 总耗时从旧 P0 的 4449s 降到 915.1s，工程修复有效。

阶段结论：

```text
P0 fix1 = completed / stable enough to continue P1 fix1
```

下一步按用户指定顺序执行：

1. 同步新修复与 P0 fix1 结果到 GitHub；
2. 重新跑完整 P1 fix1；
3. P1 稳定后再跑 P2；
4. 选定并发配置后跑 4 个 CAM 方法对照；
5. 最后整理论文结果表与 Word 初稿。

## 6. 并发探针补充：P1 fix1 与 P2

用户明确授权将 SpreadsheetBench 实验任务数据发送到外部模型 API 后，继续执行 P1/P2 并发探针。

### 6.1 P1 fix1

配置：

```text
method = CAM-Full
probe = P1 fix1
workers = 4
analyst_workers = 1
train_size = 4
batch_size = 4
epochs = 1
selection_size = 4
test_size = 4
eval_test = true
target = gpt-5.6-terra
exec_timeout = 420
seed = 42
```

输出目录：

```text
outputs/stage12_probe/spreadsheetbench_terra_cam_full_P1_w4_a1_fix2
```

结果：

```text
baseline_selection_hard = 0.0000
final_selection_hard = 0.0000
baseline_test_hard = 0.0000
final_test_hard = 0.0000
total_skips = 1
wall_time = 173.9s
total_calls = 24
```

观察：

- P1 未观察到 worker stall、批量取消或 timeout 扩散；
- 运行速度显著快于 P0 fix1；
- 但本轮所有 selection/test 结果为 0，且训练 step 为 `skip_no_patches`，没有形成可用 skill 更新。

### 6.2 P2

配置：

```text
method = CAM-Full
probe = P2
workers = 4
analyst_workers = 2
train_size = 4
batch_size = 4
epochs = 1
selection_size = 4
test_size = 4
eval_test = true
target = gpt-5.6-terra
exec_timeout = 420
seed = 42
```

输出目录：

```text
outputs/stage12_probe/spreadsheetbench_terra_cam_full_P2_w4_a2_fix2
```

结果：

```text
baseline_selection_hard = 0.0000
final_selection_hard = 0.0000
baseline_test_hard = 0.0000
final_test_hard = 0.0000
total_skips = 1
wall_time = 157.7s
total_calls = 24
```

观察：

- P2 同样未观察到 worker stall、批量取消或 timeout 扩散；
- 速度略快于 P1；
- 但质量同样坍塌为 0，且没有产生可用 patch。

### 6.3 并发配置选择

探针结果对比：

```text
P0 fix1: workers=1, analyst_workers=1, selection=0.5000, test=0.2500, wall=915.1s
P1 fix1: workers=4, analyst_workers=1, selection=0.0000, test=0.0000, wall=173.9s
P2:      workers=4, analyst_workers=2, selection=0.0000, test=0.0000, wall=157.7s
```

结论：

```text
selected_formal_concurrency = workers=1, analyst_workers=1
```

理由：P1/P2 虽然技术上稳定且更快，但本 seed 下完全没有有效成功样本或可用 patch；P0 fix1 较慢但产生非零 selection/test 信号，更适合作为正式对照实验的保守配置。

## 7. 正式 CAM 方法对照：CAM-Full fix1 中断记录

按选定并发配置启动 CAM-Full 正式对照：

```text
method = CAM-Full
workers = 1
analyst_workers = 1
train_size = 16
batch_size = 4
epochs = 1
selection_size = 8
test_size = 8
eval_test = true
target = gpt-5.6-terra
exec_timeout = 420
seed = 42
```

输出目录：

```text
outputs/formal_cam/spreadsheetbench_terra_cam_full_seed42_w1_a1_fix1
```

已完成部分：

```text
baseline_selection_hard = 0.0000
step_1_rollout_hard = 0.0000, action = skip_no_patches
step_2_rollout_hard = 0.0000, action = skip_no_patches
step_3_rollout_hard = 0.0000, action = skip_no_patches
step_4_rollout_hard = 0.0000, action = skip_no_patches
final_selection_hard = 0.0000
baseline_test_hard = 0.0000
```

状态：

```text
CAM-Full formal fix1 = incomplete / no summary.json
```

说明：

- 训练进程在 test 收尾阶段失联，输出目录没有生成 `summary.json`；
- 因没有完整 summary，fix1 不作为正式结果；
- 后续将使用新输出目录重新运行 CAM-Full formal fix2，避免覆盖 fix1 中断证据。

## 8. 正式 CAM 方法对照：CAM-Full fix2 与补评

重新启动 CAM-Full formal fix2：

```text
method = CAM-Full
workers = 1
analyst_workers = 1
train_size = 16
batch_size = 4
epochs = 1
selection_size = 8
test_size = 8
eval_test = true
target = gpt-5.6-terra
exec_timeout = 420
seed = 42
```

输出目录：

```text
outputs/formal_cam/spreadsheetbench_terra_cam_full_seed42_w1_a1_fix2
```

训练过程记录：

```text
step_1_rollout_hard = 0.0000, action = skip_no_patches, wall = 217.0s
step_2_rollout_hard = 0.0000, action = skip_no_patches, wall = 109.7s
step_3_rollout_hard = 0.0000, action = skip_no_patches, wall = 123.1s
step_4_rollout_hard = 0.0000, action = skip_no_patches, wall = 194.3s
```

状态：

```text
CAM-Full formal fix2 training = completed
CAM-Full formal fix2 summary.json = missing
```

说明：

- 训练 4 step 均完成；
- 4 个 step 均没有生成可用 patch，因此 `best_skill.md` 等价于初始/最终 skill；
- 主训练 run 仍未写出 `summary.json`，因此采用 `eval_only.py` 对 `best_skill.md` 进行独立补评。

补评配置：

```text
skill = outputs/formal_cam/spreadsheetbench_terra_cam_full_seed42_w1_a1_fix2/best_skill.md
workers = 1
exec_timeout = 420
mode = single
selection_split = valid_seen
test_split = valid_unseen
n_items_each_split = 8
```

补评输出目录：

```text
outputs/eval_only/cam_full_fix2_best_valid_seen_8_w1_a1
outputs/eval_only/cam_full_fix2_best_valid_unseen_8_w1_a1
```

补评结果：

```text
valid_seen:   hard = 0.0000, soft = 0.0000, n = 8
valid_unseen: hard = 0.0000, soft = 0.0000, n = 8
```

阶段结论：

```text
CAM-Full formal fix2 = completed via eval_only supplementation
selection_hard = 0.0000
test_hard = 0.0000
```

论文记录口径：

- 主训练过程完整完成，但没有生成主 summary；
- 因无 patch 产生，采用训练输出的 `best_skill.md` 做独立补评；
- CAM-Full 在本正式小规模设置下没有超过 baseline，且未产生可用 skill 更新；
- 该结果应作为“SpreadsheetBench + Codex target 下 CAM 反思/更新链路不稳定或低产出”的负结果报告，而不是删除。

## 9. 正式 CAM 方法对照：No-Bootstrap

配置：

```text
method = CAM-No-Bootstrap
workers = 1
analyst_workers = 1
train_size = 16
batch_size = 4
epochs = 1
selection_size = 8
test_size = 8
eval_test = true
target = gpt-5.6-terra
exec_timeout = 420
seed = 42
```

输出目录：

```text
outputs/formal_cam/spreadsheetbench_terra_cam_no_bootstrap_seed42_w1_a1_fix1
```

训练过程：

```text
step_1_rollout_hard = 0.0000, action = skip_no_patches, wall = 237.6s
step_2_rollout_hard = 0.0000, action = skip_no_patches, wall = 238.1s
step_3_rollout_hard = 0.0000, action = skip_no_patches, wall = 179.2s
step_4_rollout_hard = 0.0000, action = skip_no_patches, wall = 513.6s
```

正式结果：

```text
baseline_selection_hard = 0.0000
best_selection_hard = 0.0000
final_selection_hard = 0.0000
baseline_test_hard = 0.0000
best_test_hard = 0.0000
final_test_hard = 0.0000
test_delta_hard = 0.0000
final_test_delta_hard = 0.0000
total_steps = 4
total_skips = 4
wall_time = 3201.3s
total_calls = 56
```

阶段结论：

```text
CAM-No-Bootstrap formal = completed
selection_hard = 0.0000
test_hard = 0.0000
```

说明：

- No-Bootstrap 主 run 正常生成 `summary.json`；
- 4 个训练 step 均没有可用 patch；
- 在当前正式小规模设置下没有产生可观测性能改进。

## 10. 正式 CAM 方法对照：No-Adaptive-Budget

配置：

```text
method = CAM-No-Adaptive-Budget
workers = 1
analyst_workers = 1
train_size = 16
batch_size = 4
epochs = 1
selection_size = 8
test_size = 8
eval_test = true
target = gpt-5.6-terra
exec_timeout = 420
seed = 42
```

输出目录：

```text
outputs/formal_cam/spreadsheetbench_terra_cam_no_adaptive_budget_seed42_w1_a1_fix1
```

训练过程：

```text
step_1_rollout_hard = 0.0000, action = skip_no_patches, wall = 204.5s
step_2_rollout_hard = 0.0000, action = skip_no_patches, wall = 206.9s
step_3_rollout_hard = 0.0000, action = skip_no_patches, wall = 165.5s
step_4_rollout_hard = 0.0000, action = skip_no_patches, wall = 200.9s
```

正式结果：

```text
baseline_selection_hard = 0.0000
best_selection_hard = 0.0000
final_selection_hard = 0.0000
baseline_test_hard = 0.0000
best_test_hard = 0.0000
final_test_hard = 0.0000
test_delta_hard = 0.0000
final_test_delta_hard = 0.0000
total_steps = 4
total_skips = 4
wall_time = 2534s
total_calls = 56
```

阶段结论：

```text
CAM-No-Adaptive-Budget formal = completed
selection_hard = 0.0000
test_hard = 0.0000
```

说明：

- No-Adaptive-Budget 主 run 正常生成 `summary.json`；
- 4 个训练 step 均没有可用 patch；
- 在当前正式小规模设置下没有产生可观测性能改进。

## 11. 正式 CAM 方法对照：No-Memory

配置：

```text
method = CAM-No-Memory
workers = 1
analyst_workers = 1
train_size = 16
batch_size = 4
epochs = 1
selection_size = 8
test_size = 8
eval_test = true
target = gpt-5.6-terra
exec_timeout = 420
seed = 42
```

输出目录：

```text
outputs/formal_cam/spreadsheetbench_terra_cam_no_memory_seed42_w1_a1_fix1
```

训练过程：

```text
step_1_rollout_hard = 0.0000, action = skip_no_patches, wall = 286.5s
step_2_rollout_hard = 0.0000, action = skip_no_patches, wall = 284.8s
step_3_rollout_hard = 0.0000, action = skip_no_patches, wall = 277.9s
step_4_rollout_hard = 0.0000, action = skip_no_patches, wall = 203.8s
```

正式结果：

```text
baseline_selection_hard = 0.0000
best_selection_hard = 0.0000
final_selection_hard = 0.0000
baseline_test_hard = 0.0000
best_test_hard = 0.0000
final_test_hard = 0.0000
test_delta_hard = 0.0000
final_test_delta_hard = 0.0000
total_steps = 4
total_skips = 4
wall_time = 2634s
total_calls = 56
```

阶段结论：

```text
CAM-No-Memory formal = completed
selection_hard = 0.0000
test_hard = 0.0000
```

说明：

- No-Memory 主 run 正常生成 `summary.json`；
- 4 个训练 step 均没有可用 patch；
- 在当前正式小规模设置下没有产生可观测性能改进。

## 12. 当前正式对照实验状态

截至本记录，4 个 CAM 方法对照均已获得可记录结果：

```text
CAM-Full:               completed via eval_only supplementation, selection=0.0000, test=0.0000
CAM-No-Bootstrap:       completed, selection=0.0000, test=0.0000
CAM-No-Adaptive-Budget: completed, selection=0.0000, test=0.0000
CAM-No-Memory:          completed, selection=0.0000, test=0.0000
```

补充参照：

```text
CAM-Off valid_unseen補评: hard=0.1250, soft=0.1250, n=8
P0 fix1 probe: selection=0.5000, test=0.2500, n=4
```

初步论文解释方向：

- 正式小规模对照中，4 个 CAM 变体均未产生可用 patch；
- 在 SpreadsheetBench + Codex target 设置下，主要瓶颈不是 CAM gate 的具体开关，而是目标侧代码生成失败导致反思样本无法转化为有效编辑；
- 因此论文结果应诚实呈现为负结果/边界条件：CAM 机制在已有 benchmark 上可运行，但当前 SpreadsheetBench 真实任务与 Codex CLI 执行链路下，更新产出不足，未观察到稳定收益；
- 后续论文表格需要同时报告：探针阶段 P0 fix1 的非零信号、正式阶段 4 个 CAM 变体的 0 结果、以及 CAM-Off 补评结果，避免只展示单一口径。
