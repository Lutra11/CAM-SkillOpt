# CAM v3r1 离线修复与诊断报告

执行用户选择的方案 1：修复运行可靠性并分析现有机制证据。模型 API 请求为 **0**；测试及诊断由 GPT-6 Luna high 子 agent 使用指定的 skillopt Python 环境执行。没有重跑或回填旧四格，没有改变 CAM 决策阈值、预算算法或记忆检索算法。

## 修改与验证

- 后端在索引前验证响应结构，将空响应、SDK 超时/连接错误、错误 payload、非法工具调用和截断响应转为可定位的类型化错误。错误工件记录阶段、响应形状、请求 ID、有限堆栈位置及尝试次数，不保存响应正文或原始异常文本。禁用 SDK 隐藏重试，由封装统一约束次数；实际 SDK MockTransport 的 503→200 测试恰有两次模拟请求。
- slow update / meta skill 明确区分有效更新、带理由的不更新和失败。失败终止训练并保留阶段工件；明确不更新保留已有目标指导与优化器记忆。公共 `SlowUpdateResult` 也保留新字段。真实 trainer 循环的合成集成测试覆盖了保留状态和提前终止。
- 新修订使用独立的离线 prepare/verify 入口，递归冻结提示词 Markdown，核验源码快照与派生清单，拒绝在线运行参数。旧研究保留自己的 freeze 和 source_snapshot。
- 联合回归 **140 项唯一 unittest 全部通过**，去除了跨模块导入导致的重复收集。范围含响应契约、辅助模块、CAM 预算/门控/记忆/trainer、原 v3 runner/reporter、用量记录和 SpreadsheetBench 基础设施失败传播。

环境没有 pytest，额外装载旧 `test_types.py` 时首次遇到导入错误，现场保存在 `combined_tests_initial.log`；没有改写该旧测试或安装依赖。该文件只含普通断言方法和未使用的 pytest 导入，另以移除该未使用导入的内存 AST 兼容方式执行，24 个方法全部通过，详情见 `types_compat.log`。这不是 pytest runner 的执行结果，也不表示全仓库测试均已运行。本次新增的 `SlowUpdateResult` 往返测试属于上述正式 unittest 回归。

主要证据：`combined_tests.log`、`runtime_tests.log`、`outputs/studies/cam_v3r1_runtime_diagnostics/auxiliary_tests.log`、`freeze_tests.log`。

## 机制诊断

1. **预算来源是优先诊断点。** 8/8 步均缺乏可核验任务 ID，六步为来源缺失最小预算回退，两步在回退后进一步缩减。不能声称已检验基于独立训练任务支持数的预算机制；模型给出的 `support_count` 不替代来源证据。
2. **记忆确实进入过优化流程。** 四次类型重现机会中，三步注入过记忆，共四条。前两步属于每个 seed 的类型预热；这些描述性计数不能证明记忆改善了编辑或测试成绩。
3. **best 停在第一步，需要分析选择权衡。** 两个 CAM 的 best 均来自 step1。seed43 最终 current 验证分为 0.675，高于 best 的 0.55，但没有得到现有规则的晋升授权。不能据此推断 current 的测试成绩更好或按旧测试分放宽门控。
4. **旧七次辅助失败的根因仍未确认。** 新测试验证了相关错误类别的处理，无法重建缺失的历史响应；本修订不声称已经证明或追溯修复旧故障。

诊断协议先记录脚本哈希；分析未读取留出集预测，旧成绩报告只核对保全哈希。逐步记录见 `diagnostics.json` 和 `DIAGNOSIS.md`。

## 保全与后续边界

旧快照 1,378/1,378 文件与原 freeze 匹配；登记保护的旧 freeze、队列、results、最终验收和最终摘要保持原哈希。21 份修改前提示词副本与登记及旧观测一致，当前仅 slow/meta 两份提示词因新结果契约发生变化。这仍不能证明旧观测之前所有历史请求所用的提示词内容。

新 freeze 覆盖 1,445 份文件（含全部 21 份提示词），两次 prepare 的 SHA-256 完全相同，verify 通过。225 份修改前备份也全部通过登记哈希核对。新 freeze 和验收证据位于 `outputs/studies/cam_v3r1_runtime_diagnostics/` 及本报告目录；详见 `freeze_acceptance.json`。巡检 `cam-20` 保持 PAUSED，新修订没有运行队列。

目前完成的是可靠性修复与机制诊断，尚无新准确率结果。下一阶段优先沿“反思 → 合并 → 预算”检查训练任务 ID 的来源传递，并用单一假设的小规模训练/验证诊断检验；是否开展新实验、如何比较，须另立新协议及输出目录。旧平均差 +0.4167 个百分点、种子方向相反的结论不变。

完整协议：`docs/experiments/CAM_V3R1_RUNTIME_DIAGNOSTICS.md`。旧证据审计见 `preservation_audit.json`、`prechange_backup_audit.json`。
