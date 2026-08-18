# Stage 12 SpreadsheetBench 最小 Smoke Run 报告

生成时间：2026-08-17  
实验目录：`C:\CAM-SkillOpt\SkillOpt`  
成功输出目录：`C:\CAM-SkillOpt\SkillOpt\outputs\smoke\codex_spreadsheet_terra_v5_tiny`

## 1. 本次目标

本次任务目标是启动并完成一个最小 SpreadsheetBench smoke run，用于验证：

1. SpreadsheetBench verified 数据能够被 SkillOpt 正确加载；
2. `codex_cli` optimizer 与 `codex_exec` target 后端可以形成完整训练闭环；
3. baseline、rollout、reflect、aggregate、update、selection eval、best skill 保存等主流程不会中断；
4. Windows 环境下的 UTF-8、ACL、Codex CLI 超时/挂起问题已被最小修复。

## 2. 已确认修复

### 2.1 数据集与配置

- 修复 `env.limit` 等 `env.*` 配置项未从 YAML/CLI 正确展开的问题。
- 修复 `SpreadsheetBenchAdapter` 未把 `limit` 传给 dataloader 的问题。
- 已确认 dataloader 可以按最小规模加载 `train/val/test` split。

### 2.2 Windows ACL

原始数据目录 `data\spreadsheetbench_verified_400` 对联网/提升权限运行用户不可读，导致训练中出现 `no-test-cases`。

已授予 `desktop-7l775ct\hp` 读取权限后，提升权限训练进程可以访问 `.xlsx` 真实样本。

### 2.3 UTF-8 编码

已统一修复 SpreadsheetBench smoke 中暴露的 UTF-8 问题：

- `skillopt/model/codex_harness.py`
  - Codex CLI subprocess 使用 `encoding="utf-8", errors="replace"`。
  - Codex CLI prompt 改为 stdin 输入，避免长 prompt 命令行问题。
  - Codex CLI 工作目录改为绝对路径，避免 `-C` 相对路径嵌套。

- `skillopt/envs/spreadsheetbench/rollout.py`
  - `conversation.json`、prompt、preview、raw、code、results 等关键文件使用 UTF-8 读写。

- `skillopt/gradient/reflect.py`
  - reflection 阶段读取 `conversation.json`、prompt、preview、patch JSON 时使用 UTF-8。

- `skillopt/envs/spreadsheetbench/executor.py`
  - 生成代码临时 `.py` 文件使用 UTF-8 写入。
  - 执行 subprocess 输出使用 UTF-8 解码并容错。

- `skillopt/envs/spreadsheetbench/adapter.py`
  - `results.jsonl` 使用 UTF-8 读写。

### 2.4 Codex CLI worker stall 兜底

在 v4 中发现：当 Codex CLI 子进程卡在 Windows sandbox/tool stall 时，future 超时后底层线程仍占用唯一 worker，队列中的后续 future 永远不会启动，导致 batch runner 看似“卡死”。

已在 `run_spreadsheet_batch_codegen` 中增加兜底：一旦检测到 worker stall，把当前 batch 中尚未完成的 futures 标记为 timeout/cancelled，避免主训练流程无限等待。

## 3. 失败/中间实验记录

### v3：`outputs/smoke/codex_spreadsheet_terra_v3`

配置概要：

- `train.num_epochs=2`
- `train.train_size=8`
- `train.batch_size=4`
- `evaluation.sel_env_num=4`
- `env.exec_timeout=300`

结果：

- baseline selection 完成 4 个样本，硬通过率 `2/4 = 0.5`。
- step 1 完成 rollout、reflect、aggregate、update、selection eval。
- step 2 在 reflection 阶段失败。

直接失败原因：

```text
UnicodeDecodeError: 'gbk' codec can't decode byte 0x97 ...
```

根因：`reflect.py` 用 Windows 默认 GBK 读取 UTF-8 的 `conversation.json`。

### v4：`outputs/smoke/codex_spreadsheet_terra_v4_min`

配置概要：

- `train.num_epochs=1`
- `train.train_size=4`
- `train.batch_size=2`
- `evaluation.sel_env_num=2`
- `env.exec_timeout=240`

结果：

- baseline 两个样本均因 `task-timeout-240s` 超时。
- rollout 中 Codex CLI 出现 workspace shell/tool execution stall。
- 由于线程池 future 超时后底层 worker 没有被真正释放，主流程出现等待风险，因此手动中断。

根因：`ThreadPoolExecutor` 不能强制杀死已经卡在外部 CLI 的 worker 线程；workers=1 时，后续任务不会启动，原超时逻辑无法覆盖 queued futures。

## 4. 成功 Smoke Run：v5 tiny

输出目录：

```text
C:\CAM-SkillOpt\SkillOpt\outputs\smoke\codex_spreadsheet_terra_v5_tiny
```

配置概要：

- optimizer backend：`codex_cli`
- target backend：`codex_exec`
- optimizer model：`gpt-5.6-sol`
- target model：`gpt-5.6-terra`
- `train.num_epochs=1`
- `train.train_size=2`
- `train.batch_size=1`
- `evaluation.sel_env_num=1`
- `evaluation.test_env_num=1`
- `evaluation.eval_test=false`
- `env.mode=single`
- `env.limit=2`
- `env.workers=1`
- `env.exec_timeout=300`

### 4.1 Baseline

Selection items：1

| id | phase | hard | cases | reason |
|---|---:|---:|---:|---|
| 45635 | timeout | 0 | 0/0 | task-timeout-300s |

Baseline selection：

```text
hard = 0.0000
soft = 0.0000
```

### 4.2 Step 1

Rollout：

| id | result | cases |
|---|---:|---:|
| 32438 | FAIL | 0/1 |

Reflection / Aggregate / Update：

- failure minibatch：1 组；
- analyst：生成 1 条 edit；
- aggregate：合并为 1 条 edit；
- skill length：`1594 -> 2170`。

Selection eval：

| id | result | cases |
|---|---:|---:|
| 45635 | PASS | 1/1 |

Step 1 结果：

```text
action = accept_new_best
current_score = 1.0000
best_score = 1.0000
duration = 232.2s
```

### 4.3 Step 2

Rollout：

| id | result | cases |
|---|---:|---:|
| 398-14 | FAIL | 0/1 |

Reflection / Aggregate / Update：

- failure minibatch：1 组；
- analyst：生成 2 条 edits；
- aggregate：合并为 2 条 edits；
- skill length：`2170 -> 2917`。

Selection eval：

| id | result | cases |
|---|---:|---:|
| 45635 | PASS | 1/1 |

Step 2 结果：

```text
action = reject
current_score = 1.0000
best_score = 1.0000
duration = 251.8s
```

### 4.4 Final Summary

```text
steps = 2
accept = 1
reject = 1
skip = 0
best_score = 1.0000
best_step = 1
wall_time = 484s
total_tokens = 70,246
prompt_tokens = 68,198
completion_tokens = 2,048
calls = 8
```

## 5. 结论

本次最小 SpreadsheetBench smoke run 已完成。当前可以确认：

1. verified SpreadsheetBench 真实 `.xlsx` 数据可以被加载和执行；
2. SkillOpt 的 SpreadsheetBench 环境链路已经跑通；
3. Codex optimizer 与 Codex target executor 能完成至少一个完整 skill update 闭环；
4. Windows 编码问题已经得到最小修复；
5. batch runner 已增加 Codex CLI worker stall 兜底，避免再次无限卡住。

## 6. 下一步建议

建议暂停在这里，由用户确认后再进入下一步。

下一步可选：

1. 跑正式小规模稳定性实验：`train_size=8, selection=4, exec_timeout=420`；
2. 把本次代码补丁同步到 `C:\CAM-SkillOpt\CAM-SkillOpt` 并提交 Git；
3. 基于 smoke 结果更新论文方法论中的“实验平台与工程适配”部分；
4. 继续扩大到多 seed / ablation。
