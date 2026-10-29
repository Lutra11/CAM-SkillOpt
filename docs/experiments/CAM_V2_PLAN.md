# CAM v2r1：Qwen 成对重复与组件消融实验计划

- 修订日期：2026-10-18（Asia/Shanghai）。用户在审查后选择方案 2。
- 后续计划 1、3、4 见 [`CAM_FUTURE_PLAN_134.md`](CAM_FUTURE_PLAN_134.md)；计划 1 是当前获准的六格消融重复扩展，原九格协议不变。
- 本文取代原 v2 矩阵；旧计划及旧源码完整保存在 `_archive/v2_pre_review_20261018/snapshot/`。
- 状态：v2r1 正式九格队列已启动，当前进度见 `reports/v2r1/queue_state.json`；后续六格消融扩展另行登记，不修改本协议冻结。

## 1. 目标与结论边界

主要问题：CAM-Full 相对 SkillOpt 的改进是否在不同训练随机种子上保持方向和规模？
次要问题：移除门控方案、持久记忆、自适应编辑预算分别如何改变表现与成本？
本轮是探索性验证。v2 设计已参考旧测试结果，现有 test 不能被描述为完全未参与研究决策的确认集。
三 seed 仍不足以精确估计训练随机性，组件消融只有单 seed，只能提供有限证据。

## 2. 固定矩阵（9 个全新训练单元）

| 阶段 | Target | 方法 | Seeds | 单元数 |
| --- | --- | --- | --- | ---: |
| 主比较 | qwen | baseline / cam_full | 42、43、44，两臂成对 | 6 |
| 组件消融 | qwen | cam_no_bootstrap / cam_no_memory / cam_no_adaptive_budget | 42 | 3 |

主比较按 seed 分块；同一块的 baseline / CAM 并行，交替启动顺序，完成后再进入下一 seed。
默认 2 lane，每单元 8 workers，硬上限 3 lane。网关吞吐共享，不假设并行数与加速线性对应。
全部输出位于 `outputs/matrix_v2r1/`，报告位于 `reports/v2r1/`。
旧 baseline 和静态评测保留为历史参考，不混入新主比较。GLM、nothink、LMB、OfficeQA、gemma 暂不扩跑。
此安排将预算集中于训练重复和机制消融，而非追加模型或任务种类。

## 3. 固定协议

- optimizer=glm；target=qwen；两角色使用相同网关默认思考设置（reasoning_effort 为空，参数不发送）。
- 不把这个设置解释为关闭思考；当前后端是全局思考设置，nothink 能力梯度分析暂缓。
- SpreadsheetBench train/val/test = 80/40/280，固定题目划分；seed 改变训练抽样/顺序与门控重采样。
- 4 epochs、batch=40，共 8 个训练步；minibatch/merge=8；analyst_workers=8。
- optimizer.learning_rate=4；CAM budget=2–4。上限一致不等于实际编辑数量一致，须报告实际预算轨迹和成本。
- confidence_level=0.95、bootstrap_samples=10000、meaningful_improvement=0.0。
- soft_accept=true、drift_patience=2、drift_tolerance=0、memory_inconclusive=true。
- 不依据测试成绩追加 seed、改变阈值、挑选输出技能或缩减不利的比较。
- 使用网关模型别名；本地代码/数据冻结不等于服务端权重冻结，train.seed 也不保证远程生成确定性。这是外部后端限制。

## 4. v2r1 修复与状态语义

1. 配置：四个 v2 参数显式展平；预检仅认可训练器实际收到的配置，不回查原始 YAML 假装生效。
2. 授权：soft_accept 只授权 current；best 仍必须有 promotion_authorized，恢复时分别校验。
3. 漂移守卫：初始 best（step0）也受保护；仅 strict CAM 启用；同一 global step 的常规/slow/appendix 更新合并计数，不重复累加；计数持久化；patience=0 可关闭。
4. 记忆：提示与落盘均携带 rejected/inconclusive；不把证据不足或正向但不显著的编辑描述成失败；保留分数变化符号。epoch 内 buffer 同样区分。
5. 统计：训练步与 epoch 均分开统计 hard accept / soft accept / reject / skip。
6. 运行：输出目录进程锁、完整度验收、失败选评阶段归档而非删除；证书/协议不兼容不允许启发式清理。
7. 协议：policy=paired_selection_all_updates_v2r1，源码、配置、初始技能、题目和工作簿指纹冻结；变更后使用新协议目录。

## 5. 消融解释

- no_bootstrap：标量门控 + 自适应预算 + 持久记忆；比较的是成对门控、软接受、严格 slow-update 门控与漂移保护这套门控策略，不能归因于单个 bootstrap 操作。
- no_memory：关闭持久跨步/跨 epoch 记忆；原有 epoch 内 step buffer 和 meta-skill 保持不变。
- no_adaptive_budget：采用共享调度器的编辑预算；与 CAM-Full 比较预算策略整体影响，须同时报告实际编辑量与 token。
- 本轮不声称分别证明 soft_accept、漂移守卫和 inconclusive memory 的独立因果贡献。它们的独立开关用于未来专门消融。

## 6. 预先固定的分析

- 主指标：test_hard，即验证阶段门控授权输出技能在 test 上的准确率。CAM 的 best 是获授权的 incumbent，并非所有候选验证分数的直接最大值。
- 次指标：final_test_hard。完整报告，不选两者最大值。最终技能与 best 相同则复用逐题证据，须核验技能内容相同。
- 单 seed：同题 ID 严格配对，20,000 次题目 bootstrap CI；不一致对与 McNemar 精确 p 为探索性诊断。
- 三 seed：逐 seed 报两臂得分与配对差值，报告差值均值和样本 SD。题目 CI 不代表训练随机性；不把重复评估的 840 行当独立题目。
- 若计算跨 seed/item 重采样，必须保持每次题目抽样在 seeds 间共享；明确只有三个训练 seed，区间不稳定。
- 三个消融作为一个比较族，报告 Holm 校正 p；单 seed 消融不支持稳定性的强结论。
- 机制证据：所有门控来源、软接受数、实际回滚数、记忆 kind、命中、注入与完成响应分别报告。无命中不虚构机制贡献。
- 成本：每个进程 attempt 的返回 provider token 与墙钟累计，单列失败/重试；未返回用量和强制终止请求无法完整计费，明确覆盖范围。

## 7. 验收与恢复

完成必须同时满足：8 个连续训练步；summary/history 的动作计数一致；三份 test 逐题完整、无重复、无缺失、ID 与固定 test split 一致、均值与汇总一致。
strict CAM 要求可解析且非空的门控事件；baseline/no_bootstrap 禁止 bootstrap 产物；no_memory 禁止持久记忆库。
数据或代码变更拒绝继续旧协议。运行器启动前持有目录锁。选评身份错误只归档对应未完成阶段后重试；其它证据错误停止并保留现场。

```powershell
& "E:/MiniConda/envs/skillopt/python.exe" -X utf8 scripts/run_study.py
# 上行只预览。通过离线测试与实机冒烟后：
& "E:/MiniConda/envs/skillopt/python.exe" -X utf8 scripts/run_study.py --run --lanes 2
```

脚本在每个 seed block 完成后及全队列结束时自动更新完整度检查、CAM 分析、配对统计与结果报告。失败则标记 needs_attention，不伪装成完成。
