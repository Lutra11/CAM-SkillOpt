# Stage 12 SpreadsheetBench 正式小规模实验报告

生成时间：2026-08-17  
实验目录：`C:\CAM-SkillOpt\SkillOpt`  
输出目录：`C:\CAM-SkillOpt\SkillOpt\outputs\formal_small\spreadsheetbench_train8_sel4_t420_seed42`

## 1. 实验目的

在最小 smoke run 成功后，本实验扩大到一个仍然可控的小规模正式设置，用于验证 CAM-SkillOpt / SkillOpt 在 SpreadsheetBench verified 数据上的完整优化闭环是否能稳定运行，并观察初始 skill 经一次训练 epoch 后是否能提升 selection set 表现。

## 2. 实验配置

| 配置项 | 值 |
|---|---|
| dataset | SpreadsheetBench verified split |
| optimizer backend | `codex_cli` |
| target backend | `codex_exec` |
| optimizer model | `gpt-5.6-sol` |
| target model | `gpt-5.6-terra` |
| train size | 8 |
| selection size | 4 |
| test size | 4 configured, not evaluated in this run |
| epochs | 1 |
| batch size | 4 |
| steps per epoch | 2 |
| minibatch size | 2 |
| merge batch size | 2 |
| analyst workers | 1 |
| max analyst rounds | 1 |
| env mode | `single` |
| env workers | 1 |
| exec timeout | 420s |
| eval test | false |
| seed | 42 |

运行命令核心参数：

```text
train.num_epochs=1
train.train_size=8
train.batch_size=4
evaluation.sel_env_num=4
env.limit=8
env.exec_timeout=420
```

## 3. Baseline 结果

Selection items：4

| id | phase | hard | cases | fail reason |
|---|---|---:|---:|---|
| 45635 | exec | 1 | 1/1 |  |
| 560-12 | exec | 0 | 0/1 | eval mismatch: target cell expected `A006`, predicted `A004` |
| 55049 | exec | 0 | 0/1 | eval mismatch: `book1!B3`, expected `7040`, predicted `None` |
| 9569 | exec | 0 | 0/1 | eval mismatch: `Sheet1!AO9`, expected `0.170687841`, predicted `None` |

Baseline summary：

```text
selection hard = 0.2500
selection soft = 0.2500
```

## 4. Step 1 结果

### 4.1 Rollout

Train items：4

| id | result | cases | fail reason |
|---|---:|---:|---|
| 32255 | FAIL | 0/1 | eval mismatch: `Sheet1!D2`, expected `0`, predicted `None` |
| 577-40 | PASS | 1/1 |  |
| 50916 | FAIL | 0/1 | eval mismatch: `21-22 Schedule!C12`, expected `Homeroom`, predicted `None` |
| 10747 | FAIL | 0/1 | eval mismatch: sheet/cell with non-ASCII sheet name, expected `-10600`, predicted `None` |

Rollout summary：

```text
hard = 0.2500
soft = 0.2500
```

### 4.2 Reflection / Aggregate / Update

```text
failure groups = 2
success groups = 1
failure patches = 2
success patches = 0
merged edits = 3
ranked edits = 3
edit budget = 3
skill length: 1594 -> 3025
```

### 4.3 Selection Eval

| id | phase | hard | cases | fail reason |
|---|---|---:|---:|---|
| 45635 | exec | 1 | 1/1 |  |
| 560-12 | exec | 1 | 1/1 |  |
| 55049 | exec | 0 | 0/1 | eval mismatch: `book1!B3`, expected `7040`, predicted `None` |
| 9569 | exec | 0 | 0/1 | eval mismatch: `Sheet1!AO9`, expected `0.170687841`, predicted `None` |

Selection eval summary：

```text
candidate hard = 0.5000
candidate soft = 0.5000
action = accept_new_best
current_score = 0.5000
best_score = 0.5000
```

Step 1 timing：

```text
rollout = 557.6s
reflect = 128.2s
aggregate = 48.4s
evaluate = 399.6s
step wall time = 1133.8s
```

## 5. Step 2 结果

### 5.1 Rollout

Train items：4

| id | result | cases | fail reason |
|---|---:|---:|---|
| 32438 | FAIL | 0/1 | eval mismatch: `Sheet1!J2`, expected `18:08`, predicted `None` |
| 47766 | FAIL | 0/1 | eval mismatch: `Total (2)!K40`, expected `8000`, predicted `None` |
| 398-14 | FAIL | 0/1 | exec error: invalid character caused by mojibake apostrophe |
| 48365 | FAIL | 0/1 | exec error: invalid character caused by mojibake apostrophe |

Rollout summary：

```text
hard = 0.0000
soft = 0.0000
```

### 5.2 Reflection 结果

```text
failure groups = 2
success groups = 0
failure patches = 0
success patches = 0
action = skip
reason = no usable patches, skill unchanged
```

Step 2 没有产生新的候选 skill，因此没有进行 selection eval。

## 6. Final Summary

```text
steps = 2
accept = 1
reject = 0
skip = 1
best_score = 0.5000
best_step = 1
wall_time = 1736s
total_tokens = 221,278
prompt_tokens = 213,928
completion_tokens = 7,350
calls = 28
```

## 7. 结果解释

本次正式小规模实验得到一个清晰的正向结果：

1. 初始 skill 在 selection set 上为 `1/4 = 0.25`。
2. Step 1 通过失败轨迹分析和 patch 更新，将 selection set 提升到 `2/4 = 0.50`。
3. Step 1 的候选 skill 被接受为当前 best skill。
4. Step 2 的 rollout 全失败，且 reflection 没有生成可用 patch，因此系统正确跳过，保留 step 1 的 best skill。

这说明当前 CAM-SkillOpt / SkillOpt 链路不仅能跑通，还能在小规模 SpreadsheetBench verified 数据上观察到 selection 性能提升。

## 8. 当前主要问题

实验仍暴露出两个值得后续处理的问题：

1. Spreadsheet 任务中仍有较多 `pred=None`，说明 target agent 经常没有正确写入指定答案单元格。
2. 少数样本仍出现 mojibake 字符导致的 Python `SyntaxError`，例如智能引号被错误解码为乱码字符。这不是 UTF-8 文件写入问题，而更像是模型输出/终端文本链路中的字符污染，需要在代码抽取后做一次字符规范化。

## 9. 下一步建议

建议下一步不要马上扩大规模，而是先做一个小补丁：

1. 对从 Codex final message / raw 中抽取出的 Python 代码做字符规范化；
2. 重点替换 mojibake 智能引号、不可见控制字符和非 Python 合法标点；
3. 再复跑同配置或仅复跑 step 2 相关样本，确认 `invalid character` 类错误下降。

之后再进入：

```text
train_size=16
selection=8
exec_timeout=420
multi-seed=3
```
