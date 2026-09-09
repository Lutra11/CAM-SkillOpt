# 第 2 项：统一 CAM Gate，禁止最终 best 绕过门禁

2026-09-09。状态：**代码修复及离线验收完成，等待用户确认下一项。**

实现提交：`bc7f9f0`。实验目录与 GitHub 镜像的运行源码 SHA256 均为 `c970ef38627375a4e7107611cd447d92f03eb322da6bf158ee556dffbfda784c`，由 `scripts.cam_recovery.source_hash()` 计算。

本次只完成六步顺序中的第 2 项。没有修复调用/token tracker，没有外部模型请求，没有启动新 n=4 P0、扩大 selection 或正式消融。上轮真实 [P0 结果](P0_FINAL_REPORT.md) 保留不变，不能用新代码追认旧结果有效。

## 修复后的规则

按研究写作技能的“实现—方法一致性”检查，本次没有只改最后一个 `if score > best`，而是检查了所有可能改变 current/best 的入口：

| 路径 | CAM bootstrap 开启后的行为 |
|---|---|
| 普通 patch | 严格按样本 ID 配对；只有 CAM accept 才更新 current |
| best 晋升 | 必须有接受证据；若 incumbent best 与 current 不同，另行比较 candidate 与 best |
| appendix，包括无 body patch 的 skip 分支 | 先构造候选全文，再走 CAM，不在 reject/re_evaluate 后直接注入 |
| Slow Update | 即使旧配置为 force-accept，也必须真实 selection + CAM；不把旧分数写给新 hash |
| epoch 1 空 Slow Update 占位 | 不再改变 CAM 技能；将来非空 guidance 可以自行创建字段 |
| 最终 best | 使用同一 CAM 选择规则；final==best 时复用已接受证据，不额外重评到分数变高 |
| 缺失/错配分数 | 停止并标记 invalid_selection_evidence，不退回 baseline Gate |
| `use_gate=false` | 与 active CAM bootstrap 冲突时在请求前拒绝；显式 CAM-off/No-Bootstrap 仍保留原基线路径 |

配对证据必须满足：唯一非空 ID、三个比较对象的 ID 集合一致、数量等于固定 selection 请求数、分数有限且在 [0,1]、没有基础设施失败或非 selection 来源。返回顺序改变不再改变配对关系。

`re_evaluate` 表示当前证据不足。候选全文、对照 hash、配对 ID、待评对象 current/best 和 `automatic_retry=false` 被保存；对应的未决转移不发生，也不会作为 rejected edit 写入 Memory。没有预先授权和记录的新配对证据预算时，不自动反复重采样或重跑同一候选直到接受。

## 缓存和恢复

- selection 目录先保存 `selection_identity.json`，绑定技能全文 SHA256 与模型/selection/门禁配置。已有结果缺身份、属于另一技能或配置时拒绝复用，不给新技能贴旧成绩。
- `cam_gate_state.json` 保存当前/最佳 hash、逐项证据、决策事件及实现签名。旧 checkpoint 没有这一协议记录时必须用新目录，不能静默继承可能绕过 Gate 的 best。
- 有效 0 分不再被转换为 -1；恢复后初始 baseline 的 hard/soft 仍从真实缓存恢复。
- 损坏证据、技能 hash 不符或不兼容配置阻止续跑。拒绝恢复不会覆盖原完成结果、配置、S0 或 history；必要时另存 `resume_validation_error.json`。
- 已完成的 Slow Update 不再根据旧 action 重放文本到当前技能。
- `best_selection_hard` / `final_selection_hard` 使用真实 hard 指标；soft/mixed 的门禁分数另存 `best_gate_score`，不再冒充 hard。

## 离线验收结果

**165/165 项回归通过**：新增 Gate 40 项（纯函数 22、真实 Trainer 集成 18）＋原有 Memory/恢复/审计 125 项。新增验收耗时 1.269 秒，完整回归耗时 11.440 秒；这些是测试耗时，不是模型实验耗时。

| 合成场景 | 输入/决策 | 最终状态 |
|---|---|---|
| 旧 P0 旁路反例 | baseline=.25，candidate=.75，CI=[0,1] | `cam_re_evaluate`；best=.25、来源 initial_skill；没有空占位晋升；候选 selection 只评一次 |
| 明确改善 | baseline=0，candidate=1，CI=[1,1] | CAM accept，best=1 |
| epoch 2 Slow Update | baseline=.25，guidance candidate=.75，区间含 0；旧 force 配置开启 | 保留 initial best，不强制注入 |
| candidate/current/best 不同 | current 比较通过、best 比较不确定或拒绝 | 可更新已获许可的 current，但 best 不提升 |
| 错 ID / 缺一项 / 无效分数 / 旧缓存身份不明 | 配对证据无效 | 停止，不进入有效 test 结果计算 |
| 相同代码的有效恢复 | 0 分、已接受候选、已完成 slow 等 | 保留成绩与状态，不重放 guidance；零新增 selection 调用 |

这些评分来自**合成 target 结果和合成 optimizer 回复**，不是 SpreadsheetBench 性能。集成测试保留真实 Trainer、反思、patch 应用和 bootstrap Gate；不 mock Gate，也不允许外部网络/模型子进程。

机器可读结果：[acceptance.json](gate_step2_offline/acceptance.json)；逐项测试输出：[test_output.txt](gate_step2_offline/test_output.txt)。完整合成原始产物留在 `C:/CAM-SkillOpt/SkillOpt/outputs/recovery_20260909/gate_step2_offline_02/`。

第一版持久化测试包装器因测试名称导致 Windows 路径过长，40 项中 8 项产物写入报错；已改用短目录标识，再在全新 `_02` 目录完整通过。`gate_step2_offline_01` 的失败记录保留，没有覆盖或把其中部分成功拼接为最终验收。该问题没有触发真实模型请求。

## 复现与产物

在实验源码根目录使用已有虚拟环境，新输出目录必须不存在：

```powershell
.venv/Scripts/python.exe scripts/check_cam_gate.py --out outputs/gate_acceptance_new
```

除原有 history/summary 外，新门禁证据包括各更新目录的 `cam_gate.json`、`pending_candidate_skill.md`、`pending_re_evaluation.json`，以及运行级 `cam_gate_events.json`、`cam_gate_state.json`、`final_cam_gate.json` 和 summary 的 `cam_selection_guard`。

本次没有修改旧 P0 审计输出。独立审计器的 38 项既有回归仍通过；新 Gate 证据主要由上述专用验收和新增状态记录检查，不将旧报告的静态行号视为新代码的执行证据。

## 严格后续顺序

1. 已完成：Persistent Memory 接入与离线验收。
2. 已完成：统一 Gate 与最终 best 修复、离线验收。本轮到此暂停。
3. **下一项**：修复 target token 漏记与 optimizer tracker 重复计算，统一调用/用量统计。
4. 全部代码冻结后，用相同样本、模型、reasoning、split、seed、timeout、并发重新验收并跑 n=4 P0；不复用旧源码的运行结果冒充新实验。
5. **在扩大 n=8/16 之前**，真实运行必须自然产生 Memory 写入，随后检索命中，并进入实际 optimizer 提示词。离线合成证据、预塞历史或只执行检索但未命中都不能替代这一门槛。
6. 只有 Gate、Adaptive Budget、Memory 都在真实运行中有效触发，才扩大规模并运行正式消融、多 seed。

单 step 冷启动 P0 不保证有过去记录可命中；若新 P0 没有自然 Memory 链路，继续保持未放行，先在 n=4 范围内明确下一项小规模验证方案，不能直接扩大 selection 来绕过此要求。
