# 正式实验前的实现核查清单

2026-09-09。范围：代码与恢复探针证据审计，不是方法性能结论。P0 运行源码 SHA256：`524ca3716b4457a47f09c5baeb496a4f254d6b3852a01ec7e2bd01a66d6cadad`。本文件不改变冻结的运行代码。

## 1. 持久化 Memory 尚未接入检索

`skillopt/engine/trainer.py:1016` 初始化 PersistentRejectedEditMemory；拒绝分支在 `:1734` 写入。但 trainer 没有 `cam_memory.retrieve(...)` 调用，检索方法仅在 `skillopt/cam/rejected_memory.py:128` 定义。短期 step_buffer 不是该持久化检索路径。

在真实读取、写入、命中与上下文注入可以观测之前，不能把 CAM-Full 与 No-Memory 视为已成立的机制对照。没有检索埋点时，调用/命中数量为未知（null），不能伪装为已测得的 0 次。

## 2. 最终 best 的提升不一定经过 CAM Gate

已确认的实现顺序：

1. `trainer.py:1596` 的 `cam_re_evaluate` 保留原 current/best，不接受候选；该分支没有立即补评循环。
2. `trainer.py:1851` 在 epoch 1 注入空 Slow Update 占位，改变 current 的文本/哈希。
3. `trainer.py:2268` 对该 current 重新评 selection；`:2293` 只要分数严格高于 best，就直接提升 best，没有调用 paired_bootstrap_gate。

本轮 `p0_newcli_retry_01` 已实际发生：baseline=0.25，patch 候选=0.75，但差值区间 `[0,1]` 包含 0，CAM 返回 re_evaluate；随后“初始技能＋空占位”重新评估得 0.50，被提升为 best，origin=`slow_update_placeholder_epoch_01`。这不是 0.75 候选被接受。后续 best/test 分数不能归因为那 3 处 patch 的采用，也不能把 0.25→0.50 归因为已证实的学习效果。

“存在独立于 bootstrap 的最终提升路径”是代码事实。是否违反方法设计需对照研究方案：若所有 best 更新都必须经过 CAM，则需统一门禁；若允许额外的 validation-argmax 选择，则应明确披露，并在所有公平对照中控制额外评估预算和模型选择流程。不能在当前 P0 中静默修改实现或重写历史结果。

## 3. 调用与 token 统计存在两项限制

- `skillopt/model/codex_harness.py:983` 将 target tracker token 记录为 0；原始 `turn.completed.usage` 才能提供已观测用量。
- `skillopt/model/__init__.py:362` 与 `:373` 相加 Codex/Claude 的同一个 common tracker，可能将 optimizer 调用/token 双算。本轮反思实际 2 次、合并实际 1 次，而 step_record 分别显示 4 和 2。

恢复审计器按每次请求的一份 canonical raw trace 独立计数，不按重复 trace 文件累计，不直接把 tracker 总值除以 2。失败请求或缺失的 usage 保持未知；没有账单/价格依据时货币成本为 null。正式多 seed 矩阵前应修复并统一计量入口。

## 放行边界

完整 P0 即使通过，也只证明低成本端到端链路与产物可以运行。它不能替代上述实现核查、同代码/模型/reasoning/split/seed/timeout/并发的公平矩阵，也不能替代至少 3 个 seeds 的正式证据。先完成本轮 P0 审计，再单独处理这些前提；不立即启动四组正式消融。
