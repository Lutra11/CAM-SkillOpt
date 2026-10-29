# CAM v3r1：运行可靠性修复与离线机制诊断

用户选择的方案 1。修订名为 `cam_v3r1_runtime_diagnostics`；本阶段只修复运行结果的可判别性、冻结完整性，并分析已有训练与验证记录。模型请求预算为 **0**。测试与诊断由 GPT-6 Luna high 子 agent 使用 `E:/MiniConda/envs/skillopt/python.exe -X utf8` 执行。

## 旧实验及解释边界

`continuous_adaptation_v3` 的四格已经验收完成。baseline/CAM 在 seed42 为 68/64 题，seed43 为 65/70 题；每格使用相同的 120 题。平均配对差为 CAM +0.4167 个百分点，两个 seed 方向相反，不能据此认定稳定收益。重复使用的 120 题不能计为 240 道独立题。

旧实现的主链为：训练失败/成功轨迹 → 记忆辅助反思 → 合并编辑 → 自适应预算 → 排序及应用 → 验证门控更新 current/best → 最终评估 best。slow update 在 epoch 边界生成目标侧指导，meta skill 生成后续优化器侧记忆；两臂均使用这些辅助模块。CAM 的拒绝记忆是另一个组件，不能与 meta skill 混为一谈。

旧研究记录了七次辅助生成失败，却被写为 `no_content` 并继续。其 `NoneType` 签名已知，但底层服务响应和内部异常证据缺失，无法确认是超时、错误响应、空 choices 或其他原因。本修订验证这些故障类别会被正确处理，不声称已证明七次旧故障的根因，也不回填其缺失更新。

旧 freeze 未包含提示词 Markdown。后补的 21 份提示词观测不能证明全部历史请求的内容。本修订保留原始 freeze、source_snapshot、队列和成绩，在独立目录登记修改前快照及新 freeze；不将两个版本成绩合并。

## 本阶段修改

1. **后端响应与重试。** 在读取响应前验证内容和结构；合法的工具调用可没有正文。异常转为带阶段和有限安全元数据的 `InfraError`，避免空结构触发无法定位的 `NoneType`。由后端统一约束尝试次数，避免 SDK、调用封装和辅助模块叠加重试。认证、模型不可用和确定性请求错误及时失败。
2. **辅助结果契约。** 区分 `validated_update`、`validated_no_update` 与失败。明确不更新必须给出理由；保留当前指导或上一份成功 meta memory。无效 JSON、缺字段、空值和异常不能冒充正常不更新；失败保留阶段工件并终止该次训练，不产生正常完成成绩。兼容旧格式中有效的非空内容对象。提示词也登记了新输出契约，因此未来生成行为属于新修订。
3. **完整冻结。** 新入口仅提供离线 `--prepare` / `--verify`，拒绝 `--run` 和 `--cell`。递归记录提示词 Markdown，以及源码、配置、测试、初始技能和数据；同时核验实际快照与派生任务清单。源文件和快照的增加、缺失或变动均须显式处理。新目录没有运行队列，不沿用旧认证预检作为新启动授权。
4. **机制诊断。** 只读取旧训练/验证历史、任务顺序、门控、预算、记忆和辅助结果。旧成绩文件只核对保全哈希；不读取留出集预测，不搜索门控阈值，不重放假想训练轨迹。诊断协议先登记脚本哈希，再生成报告。

这些是可靠性和证据修复；没有修改 bootstrap、分组保护、预算规则或记忆检索规则。遇到真实生成故障时，新版本会停下而旧版本可能继续，这是有意登记的运行语义差异。

## 已有机制证据与下一步准入

已有记录显示，CAM 的 8/8 步预算来源未知，均触发来源缺失回退（其中两步进一步触发有害编辑后的缩减）。因此不能说“基于独立任务支持数的预算”已得到有效检验。应先确认反思与合并链如何携带可核验的训练任务 ID；仅有 `support_count` 或模型自报支持数不等于任务来源证据。本阶段记录这个断点，不修改编辑归因算法。

四次任务类型重现中有三次注入记忆，共四条。前两步是每个 seed 的类型预热；不能把它们当作检索失败。类型重现也不保证必有合格历史记忆，因此 3/4 是描述性覆盖率，不是预设必须达到的成功率。

两个 CAM 的最终 best 均来自第一步。seed43 的最终 current 验证分虽高于 best，仍未得到晋升授权。该现象提示需要分析门控取舍，但不能据此断言 current 的测试分更高，也不能用已看过的测试集挑选放宽规则。

本阶段验收后，下一步应先选一个可检验假设，固定一个小规模训练/验证诊断，再决定是否进入新的成对实验。例如优先检验预算的任务来源传递，而不是同时改三个组件。需要新的方法设计、冻结和输出目录；本入口不会自动启动这些实验。

## 产物与离线命令

- 修改前证据：`outputs/studies/cam_v3r1_runtime_diagnostics/prechange/` 和 `prechange_manifest.json`。
- 新完整冻结：同目录的 `freeze.json`、`source_snapshot/`、`task_orders/`、`resolved_configs.json`、`test_subset.json`。
- 机制诊断：`reports/cam_v3r1_runtime_diagnostics/diagnostic_protocol.json`、`diagnostics.json`、`DIAGNOSIS.md`。
- 修复验收与测试：`reports/cam_v3r1_runtime_diagnostics/` 中的测试日志及修复报告。

```powershell
& "E:/MiniConda/envs/skillopt/python.exe" -X utf8 scripts/diagnose_cam_v3_mechanisms.py
& "E:/MiniConda/envs/skillopt/python.exe" -X utf8 scripts/run_continuous_adaptation_v3r1.py --prepare
& "E:/MiniConda/envs/skillopt/python.exe" -X utf8 scripts/run_continuous_adaptation_v3r1.py --verify
```

`--prepare` 只在源文件稳定、相关离线测试通过后执行。旧巡检 `cam-20` 保持 PAUSED；不得用旧启动命令恢复已完成实验。
