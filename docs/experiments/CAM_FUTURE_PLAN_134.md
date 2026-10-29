# CAM 后续实验计划：1、3、4

- 记录日期：2026-10-18（Asia/Shanghai）
- 执行顺序：计划 1 → 计划 3 → 计划 4。
- 计划 1 已获用户选择并开始准备；计划 3、4 先登记，待前一阶段结果和资源情况复核后再启动。

## 计划 1：补齐消融训练重复（当前执行）

将三个组件消融都扩展到三个训练种子。原 `cam_v2r1_qwen` 的 seed 42 结果作为每个消融的 seed 42；新增 seed 43、44：

| 方法 | 已有 seed | 新增 seed | 新增单元 |
| --- | --- | --- | ---: |
| cam_no_bootstrap | 42 | 43、44 | 2 |
| cam_no_memory | 42 | 43、44 | 2 |
| cam_no_adaptive_budget | 42 | 43、44 | 2 |
| **合计** |  |  | **6** |

完成后共 15 个训练单元：baseline/CAM-Full 各 3 seed（6 格），三个消融各 3 seed（9 格）。新增格沿用 v2r1 的 target、optimizer、数据划分、训练设置和端点定义；每格使用独立输出目录与扩展 manifest，原 9 格的冻结清单与队列状态保持原样。扩展最多 2 lane，并等待原 driver 成功结束且 9 格全部通过完整性验收后再运行。父实验失败或缺格时，扩展停在 `needs_attention`，保留证据并调查。

新增 test 结果可见前固定以下分析口径：

- 每个 seed 分别报告 CAM-Full 与消融的 `test_hard`、`final_test_hard`、逐题成对差值和成本；`test_hard` 是主端点。
- 每个消融在每个 seed 内按相同 test task ID 计算 `ablation - CAM-Full` 配对差（正值表示消融更好，负值表示 CAM-Full 更好），再报告三个 seed 的差值、均值和样本标准差。训练 seed 数只有 3，跨 seed 推断均标成探索性，不把 840 条重复任务评测当成独立训练重复。
- 每个 seed 的任务级不确定性用配对任务 bootstrap 描述。若汇总跨 seed 与任务的区间，同一次 bootstrap 必须在各方法/种子间共享抽中的 task ID，并明确这不能弥补训练 seed 数少。
- McNemar 检验按 seed 作为任务级诊断；对每个 seed 内三个消融比较组成的 3 项族做 Holm 校正。调整后的值只有该 seed 三项比较均通过完整性验收时才报告。显著性结果不替代三 seed 的描述性差值。
- 同时报告机制事件、实际编辑预算、已返回 provider token、失败尝试和墙钟时间；远端模型别名的权重与服务状态无法冻结。

执行前门槛：

1. 原 9 格 driver 退出码为 0、队列状态为 complete，且每格通过 `scripts.experiment_integrity.validate_cell`。
2. 扩展六格的配置指纹与父协议一致，差异仅限 method、seed、输出路径及消融自身规定的 CAM 开关。
3. 启动第一格之前保存扩展 manifest、配置指纹、父 freeze 摘要和计划版本；协议不符即停止，不自动修补配置。

## 计划 3：相同调用预算下比较 baseline 与 CAM-Full

研究问题：CAM-Full 的收益在相同推理与优化器 API 成本下是否仍然存在，从而区分策略效果与更多可用调用预算。

只比较 baseline 与 CAM-Full，按三个 seed 成对运行。预算单位固定为训练/验证阶段 optimizer 与 target 请求返回的 input+output token 总数；缓存 token 是 input 的子集，不重复相加。累计值跨进程重试与恢复，held-out test 评测在预算之外并单独计费。预算在完整训练步边界检查，因此是共同上限并允许最多一个完整训练步超出；报告每格实际使用量与超出量，不声称 token 数完全相等。

先在 `cam_v2r1_qwen_matched_budget` 新协议中新增两格无 test 的成本校准（seed42 baseline、CAM-Full），完整执行 8 步并记录阶段级请求与用量。若关键阶段用量不可完整核算，暂停并报告预算控制不可审计。预先固定标准上限 `B` 为两格完整 8 步训练/验证 token 总数的中位数，较低上限为 `0.5B`。随后按预算水平×方法×seeds42/43/44 成对运行，共 12 格；独立协议实现持久用量账本、恢复去重与步边界停止，并离线验证暂停、恢复、超限与未知用量处理。完整计划 3 共 14 格（2 校准 + 12 正式），预算水平不依据测试分数选取。

固定数据、划分、训练设置、验收门槛和主端点，使用独立 freeze 与输出目录。各方法达到共同预算上限前 validation-authorized incumbent 作为最终技能，在独立 test 阶段评测。报告两档预算的质量、逐 seed 配对差、实际 token、optimizer/target 请求数、完成步数、重试与墙钟时间，并注明失败/重试中未返回用量可能无法完整计费。该方案估计带一个训练步粒度的固定成本上限下的系统表现，完成步数可能随方法和预算而不同。

## 计划 4：独立任务集泛化验证

研究问题：在未参与 v2r1 调参与方案选择的新任务集上，验证 baseline 与 CAM-Full 的差异能否迁移。

保持 Qwen target、GLM optimizer 和三 seed 成对设计，baseline/CAM-Full 共 6 格。当前选定 SearchQA：本仓库已有固定的 400/200/1400 train/val/test 划分，检查发现 2,000 个 ID 均唯一、跨 split 无 ID/精确 question-answer/context 重复，且 test ID 与 SpreadsheetBench 无交集；仓库没有先前 SearchQA 结果目录。该 split 是 SearchQA 上游 validation 的确定性子样本，因此报告只能称为独立 benchmark 上的本地 held-out test，不能称作官方 upstream test。

为对齐 v2r1 的训练规模，预先从 SearchQA train split 确定性抽取 80 题、从 val 抽取 40 题，完整保留 1,400 test 题。对每个 train ID 计算 `SHA256(UTF-8("cam_v2r1_qwen_searchqa_v1:train:" + id))`，对每个 val ID 使用相同规则并把 `train` 换成 `val`；按摘要十六进制小写字符串升序排序，摘要相同时按原始 ID 升序，分别取前 80/40 条。扩展内所有方法/seed 共用完全相同的 80/40/1400 任务。配置使用 4 epochs、batch 40（8 训练步）、Qwen target、GLM optimizer、与 v2r1 相同的 reasoning 参数与学习/门控设置。主端点为归一化 exact-match `test_hard`，F1 与 `final_test_hard` 作描述性次指标。先锁定派生 split、task IDs、数据与评分器指纹，再冻结新协议。

训练集与验证集用于学习和门控，独立测试集只在训练策略、阈值、分析和预算固定后评测。六格使用独立 `cam_v2r1_qwen_searchqa` 协议、manifest、源码/数据 freeze 和输出目录。预注册主端点、逐 seed 配对统计、失败处理、成本指标和多重比较方案。该 benchmark 与 SpreadsheetBench 领域差异明显，结论描述跨任务迁移，不解释为上游官方 benchmark 排名。

新 benchmark 的接口、工具权限和评分标准可能让环境效应与方法效应混在一起。正式运行前先做不含正式测试题的小规模兼容性检查，并审计任务重叠与评分器。

## 依赖顺序

计划 1 完成后先审查 15 格质量、成本和组件稳定性。计划 3 用于成本公平性，计划 4 用于外部泛化；两者使用独立协议目录与冻结，不修改已有 v2r1 证据。若计划 1 暴露协议或验收问题，先修复并以新版本登记，不回写旧单元。
