# CAM-SkillOpt Codex CLI 全链路实验方案

更新时间：2026-08-17

## 1. 实验目标与模型矩阵

本实验目标是在官方 SkillOpt 训练协议基础上，验证 CAM-SkillOpt 在技能优化过程中的可靠性收益。CAM-SkillOpt 包含三类机制：

1. Paired Bootstrap Gate：基于成对样本分数的 bootstrap 置信门控，降低噪声更新被接受的概率。
2. Adaptive Budget：根据失败置信度动态调整优化预算。
3. Rejected Memory：保存被拒绝的编辑证据，减少重复无效探索。

正式实验矩阵：

| 维度 | 取值 |
| --- | --- |
| 任务 | SearchQA、DocVQA、SpreadsheetBench |
| Optimizer | `gpt-5.6-sol` |
| Target | `gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna` |
| 方法 | Static、SkillOpt、CAM-Full |
| Seed | 42 |

主实验共 `3 tasks × 3 targets × 3 methods = 27` 个单元。额外在 `SpreadsheetBench + gpt-5.6-terra` 上做 3 个消融：

- No-Bootstrap
- No-Adaptive-Budget
- No-Rejected-Memory

合计 30 个实验单元。Static 不训练，只做最终评估。

## 2. 官方 4-epoch 配置说明

正式训练严格对齐 SkillOpt 默认协议：

```yaml
train:
  num_epochs: 4
  batch_size: 40
  accumulation: 1

gradient:
  minibatch_size: 8
  merge_batch_size: 8
  max_analyst_rounds: 3

optimizer:
  learning_rate: 4
  min_learning_rate: 2
  lr_scheduler: cosine
  use_slow_update: true
  slow_update_samples: 20
  use_meta_skill: true
  skill_update_mode: patch
```

官方默认 analyst workers 为 16。Codex CLI 版本先使用 smoke run 验证并发稳定性；如果出现限流、超时或进程竞争，再将正式实验并发降为 4 或 2，并在实验记录中注明这是执行层并发调整，不是算法超参数变化。

## 3. Codex CLI 接入方式

新增模型配置：

```yaml
model:
  optimizer_backend: codex_cli
  target_backend: codex_exec
```

要求：

- optimizer 通过 `codex exec --json` 调用；
- target 继续使用现有 Codex workspace harness；
- optimizer 使用 read-only sandbox；
- target 使用 workspace-write sandbox；
- 默认关闭 network 和 web search；
- 保存 Codex 原始 trace、optimizer 原始响应、token、耗时和错误信息；
- 不依赖 `OPENAI_API_KEY`，优先使用本机 `codex login` 的 ChatGPT/Codex 登录态。

当前实现说明：

- `optimizer_backend=codex_cli` 已注册到 `skillopt.model.backend_config` 和 `skillopt.model.__init__`。
- optimizer 复用 `skillopt/model/codex_backend.py` 中的 `codex exec --json` 调用路径。
- target 复用 `skillopt/model/codex_harness.py` 中的 `codex_exec` workspace harness。

## 4. Smoke run 命令

第一轮 smoke 只跑 SpreadsheetBench 全链路，使用 terra target、2 epoch、8 个训练样本：

```powershell
python scripts/train.py `
  --config configs/spreadsheetbench/default.yaml `
  --cfg-options `
  model.optimizer_backend=codex_cli `
  model.target_backend=codex_exec `
  model.optimizer=gpt-5.6-sol `
  model.target=gpt-5.6-terra `
  train.num_epochs=2 `
  train.train_size=8 `
  train.batch_size=4 `
  train.seed=42 `
  gradient.minibatch_size=2 `
  gradient.merge_batch_size=2 `
  gradient.analyst_workers=1 `
  gradient.max_analyst_rounds=1 `
  optimizer.slow_update_samples=2 `
  evaluation.sel_env_num=4 `
  evaluation.test_env_num=4 `
  evaluation.eval_test=true `
  env.mode=single `
  env.limit=8 `
  env.workers=1 `
  env.exec_timeout=300 `
  model.codex_exec_use_sdk=cli `
  model.codex_exec_sandbox=workspace-write `
  model.codex_exec_full_auto=false `
  model.codex_exec_network_access=false `
  model.codex_exec_web_search=false `
  env.out_root=outputs/smoke/codex_spreadsheet_terra
```

## 5. Smoke 验收标准

Smoke run 成功的必要条件：

- 完成 epoch 1 和 epoch 2；
- 至少生成一个合法 optimizer patch；
- `selection_eval` 能正常评分；
- SpreadsheetBench 的 `solution.py` 能执行；
- `slow_update/epoch_02/` 存在；
- `meta_skill/epoch_02/` 存在；
- `summary.json`、`history.json`、`config.json` 和 token 统计存在；
- Codex CLI 超时、空响应、非法 JSON 能被记录；
- 使用相同命令重新启动时能够断点恢复；
- 日志和配置中不存在 API Key。

## 6. 正式实验命令

Smoke 通过后，按任务、target、方法展开 30 个独立输出目录。每个实验单元必须使用相同数据划分、epoch、batch 和 seed。

示例命名：

```text
outputs/final/searchqa/terra/static_seed42
outputs/final/searchqa/terra/skillopt_seed42
outputs/final/searchqa/terra/cam_full_seed42
outputs/final/spreadsheetbench/terra/cam_no_bootstrap_seed42
```

如果 CAM-Full 相比 SkillOpt 提升小于 2 个百分点，则补跑 `SpreadsheetBench + terra` 的 seed 43 和 seed 44。

## 7. 输出目录和结果表

每个实验单元单独保存：

- `config.json`
- `summary.json`
- `history.json`
- token/cost/runtime 统计
- Codex CLI raw trace
- optimizer raw response
- selection/test evaluation 明细
- 最终 skill 文件

最终论文表格至少包含：

| Task | Target | Method | Seed | Test score | Δ vs Static | Δ vs SkillOpt | 95% CI | Calls | Tokens | Runtime | Failure rate |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- | ---: |

## 8. 故障排查与停止条件

先停止并记录的情况：

- Codex CLI 无法登录或无法启动；
- 单样本 target exec 无法生成有效答案；
- optimizer 连续返回空响应或非法 JSON；
- SpreadsheetBench `solution.py` 执行失败且不是任务本身错误；
- 输出目录缺少关键文件，导致无法复现；
- 日志中出现真实 API Key。

排查优先级：

1. 配置解析 dry-run；
2. Codex CLI 单次 optimizer ping；
3. Codex exec 单样本 eval-only；
4. SpreadsheetBench 2-epoch smoke；
5. SearchQA/DocVQA 单样本 eval-only 探针；
6. 正式 30 单元实验。
