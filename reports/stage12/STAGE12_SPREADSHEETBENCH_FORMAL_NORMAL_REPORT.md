# Stage 12 SpreadsheetBench 正常小规模正式实验报告

生成时间：2026-08-18  
实验目录：`C:\CAM-SkillOpt\SkillOpt`  
输出目录：`C:\CAM-SkillOpt\SkillOpt\outputs\formal_normal\spreadsheetbench_train16_sel8_t420_seed42`

## 1. 实验状态

本轮实验已完成，不是卡住。

训练输出已生成：

- `summary.json`
- `steps\step_0001\step_record.json`
- `steps\step_0002\step_record.json`
- `steps\step_0003\step_record.json`
- `steps\step_0004\step_record.json`

## 2. 修复内容

本轮先修复了上一次实验暴露出的生成代码乱码 / 自然语言误执行问题。

修复文件：

```text
C:\CAM-SkillOpt\SkillOpt\skillopt\envs\spreadsheetbench\codegen_agent.py
```

主要修复：

1. 对 Codex 生成代码做 Unicode 规范化；
2. 替换智能引号、智能双引号、BOM、零宽字符、不可见格式字符；
3. 处理常见 mojibake 乱码片段；
4. 增加 Python 代码有效性判断；
5. 如果 Codex 返回的是普通自然语言，例如 `I'm blocked ...`，不再误当作 Python 执行，而是记录为 `empty-code-block`。

修复后，本轮实验没有因为 mojibake 字符导致训练主流程崩溃。

## 3. 实验配置

| 配置项 | 值 |
|---|---|
| dataset | SpreadsheetBench verified split |
| optimizer backend | `codex_cli` |
| target backend | `codex_exec` |
| optimizer model | `gpt-5.6-sol` |
| target model | `gpt-5.6-terra` |
| train size | 16 |
| selection size | 8 |
| epochs | 1 |
| batch size | 4 |
| steps per epoch | 4 |
| minibatch size | 2 |
| merge batch size | 2 |
| analyst workers | 1 |
| max analyst rounds | 1 |
| env mode | `single` |
| env workers | 1 |
| exec timeout | 420s |
| eval test | false |
| seed | 42 |

## 4. Baseline 结果

Baseline selection hard：

```text
0.2500
```

也就是：

```text
2 / 8
```

baseline 通过样本：

- `45635`
- `463-17`

baseline 失败样本：

- `560-12`
- `55049`
- `9569`
- `7902`
- `227-40`
- `54144`

## 5. 训练过程结果

### Step 1

```text
rollout hard = 0.2500
selection hard = 0.0000
action = reject
```

Step 1 的候选 skill 低于 baseline，因此被拒绝。

### Step 2

```text
rollout hard = 0.0000
selection hard = 0.5000
action = accept_new_best
```

Step 2 的候选 skill 把 selection 表现从 `0.25` 提升到 `0.50`，因此被接受为 best skill。

Step 2 selection 通过样本：

- `45635`
- `227-40`
- `463-17`
- `54144`

### Step 3

```text
rollout hard = 0.5000
selection hard = 0.5000
action = reject
```

Step 3 与当前 best 持平，没有超过 gate，因此被拒绝。

### Step 4

```text
rollout hard = 0.5000
selection hard = 0.3750
action = reject
```

Step 4 低于当前 best，因此被拒绝。

## 6. Final Summary

```text
steps = 4
accept = 1
reject = 3
skip = 0
best_score = 0.5000
best_step = 2
wall_time = 5564.8s
total_calls = 77
total_tokens = 419,160
prompt_tokens = 403,894
completion_tokens = 15,266
```

## 7. 核心实验结论

本轮正式实验得到正向提升：

```text
baseline selection hard = 0.25
best selection hard = 0.50
absolute improvement = +0.25
relative improvement = +100%
```

在 SpreadsheetBench verified 真实 `.xlsx` 数据上，当前 CAM-SkillOpt / SkillOpt 链路能够通过失败轨迹反思、patch 聚合和 skill 更新，将 selection set 表现从 `2/8` 提升到 `4/8`。

## 8. 当前局限

1. 本轮是单 seed 实验，还不能作为最终统计显著结果；
2. 本轮未执行 held-out test evaluation；
3. 部分任务仍出现 `pred=None`，说明 target agent 对复杂表格定位、答案单元格写入、公式推导仍不稳定；
4. Windows + Codex CLI 环境偶发 sandbox/tool stall，虽然已有 timeout 与 worker-stall 兜底，但会增加 wall time。

## 9. 建议下一步

建议下一步优先做：

1. 用当前 best skill 追加 held-out test evaluation；
2. 把本轮修复代码和报告同步到 `C:\CAM-SkillOpt\CAM-SkillOpt`；
3. 再做 `seed=43/44` 多 seed 复现实验；
4. 最后再扩大到 `train_size=32, selection=16`。
