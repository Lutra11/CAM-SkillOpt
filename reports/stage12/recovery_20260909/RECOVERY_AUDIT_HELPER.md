# Recovery P0 审计工具与后续前提

工具：`../../../scripts/audit_recovery_run.py`。这是一次性离线审计器，不轮询、不启动模型、不导入运行中的实验模块，也不修改原实验目录。运行中的快照可能不完整，不据此标记完成。

## 完成后使用

```powershell
& 'C:\CAM-SkillOpt\SkillOpt\.venv\Scripts\python.exe' `
  'C:\CAM-SkillOpt\CAM-SkillOpt\scripts\audit_recovery_run.py' `
  --run 'C:\CAM-SkillOpt\SkillOpt\outputs\recovery_20260909\p0_http_01' `
  --reference 'C:\CAM-SkillOpt\SkillOpt\outputs\stage12_probe\spreadsheetbench_terra_cam_full_P0_w1_a1_fix1' `
  --json-out 'C:\CAM-SkillOpt\CAM-SkillOpt\reports\stage12\recovery_20260909\P0_HTTP01_FINAL_AUDIT.json' `
  --md-out 'C:\CAM-SkillOpt\CAM-SkillOpt\reports\stage12\recovery_20260909\P0_HTTP01_FINAL_AUDIT.md'
```

已有导出默认不覆盖；如确需刷新相同报告路径，可显式加 `--overwrite`。原始 run 目录禁止作为导出位置。JSON 的 per_task_metrics 仅含任务 ID、数值分数、失败类型和产物存在性，不含任务正文、提示词、表格数据、代码响应或凭据。原始 trace 留在本地实验目录。

## 统计口径

审计 schema=2 的本地 core gate 要求：完整的四样本协议、一个完整训练 step、完整且分数一致的 selection/test、非空 assistant 轨迹与 raw 文件、已完成 optimizer 请求、有 payload 的完成反思组、当前源码与 manifest 一致、无解析缺漏及无基础设施事件。缺失项为 false；未知源码为 null，均不能通过。机器重启/进程连续性仍须主任务核对外部开始/结束快照，工具不把本地 core gate 当作完整机器验收。

- 完成任务数、n_scored、llm/code/exec 与分数分开记录。完成计分流程不等于任务成功。
- 每阶段同时报告 expected、实际行数、唯一 ID 数、有效评分数。跨阶段复用相同任务不增加独立样本量。n=4 是探索性探针。
- 失败阶段中的已完成任务均分仅作诊断；任何 infra_error 都阻止整轮结果进入比较。
- patch 产出率按“实际调用的反思组中有 edits payload 的组数 / 实际调用组数”，不把 stats JSON、None、空 patch 算成有效 patch。旧结果缺少 request_meta 时不凭空补 analyst 调用数。
- CAM Gate 计数来自 step_record.cam_gate_used；预算来自 cam_failure_confidence 与实际 edit_budget。配置开关不构成激活证据。第一条预算对比注明是“配置初值→首次计算值”，不是已观测的前一步变化。
- Memory 写入来自 trajectory_digest.cam_memory_items_added，持久化条目数来自 cam_rejected_memory.json；不输出其中内容。无检索埋点时 retrieval_calls / retrieval_hits 为 null。
- target 只读每个 prediction 目录的一份 canonical `codex_raw.txt`，缺失时才选 infra/raw_trace 文件；raw.txt / raw_trace 副本不会重复计费。重复 results 行也不会重复计算原始 token。optimizer 只读每个 model_calls 请求目录的一份 raw_trace。只解析 turn.completed.usage，失败请求缺少用量时明确为不完整。
- input_tokens 已包含 cached_input_tokens，汇总使用 input+output，不将缓存输入重复相加。没有价格、账单或失败用量时，货币成本保持 null。
- token 的 complete coverage 要求每个已完成事件同时存在非负 input_tokens 和 output_tokens，且没有失败事件；只有 output_tokens 的记录不会把缺失输入当作零或声称完整。
- final selection/test 可能复用已有结果；字段相同不意味着新增独立评估。工具用本地技能内容指纹、来源任务记录及分数验证同技能复用，final test 还要求保存的 reuse summary marker；证据不足返回 missing_or_unproven 并拒绝 core gate。

## 已确认的 P2 前提（本轮运行源码未修改）

1. PersistentRejectedEditMemory 在 trainer 中有初始化与拒绝后写入调用，但没有 `cam_memory.retrieve(...)` 调用。其 retrieve 方法目前仅在 `skillopt/cam/rejected_memory.py` 定义。trainer 的 step_buffer_context 是另一种短期上下文路径，不能替代对该持久化模块检索/命中的验证。任务 1–5 的链路验收可以与此分开；按用户要求，正式 CAM-Full / No-Memory 比较必须等到真实检索与使用已接入、可观测后再开始。
2. `skillopt/model/codex_harness.py` 把 target rollout token 记为 0，真实 turn.completed.usage 仍可能存在于 raw trace。
3. Codex 与 Claude 后端导出的均是 common.tracker，但 `skillopt/model/__init__.py:get_token_summary()` 将两者相加，所以 trainer 中 optimizer 的调用/用量可能双算。工具保留原 tracker 数字并另列 canonical raw 数字，不静默给总成本除以 2。

以上源码位置的当前精确行号由工具动态生成在 source_evidence 中，且保存与 run manifest 的源码 hash 一致性，避免把后续改动误归于旧运行。工具本身位于 GitHub 副本目录，不在当前实验 source_hash 的扫描范围内。

历史 P0 fix1 的 2 个 patch、1 次 CAM Gate 与 budget=3 可从原始产物复核；它的 gate 决策是 re_evaluate，未触发拒绝后的 Memory 写入，不能据配置宣称 Memory 已生效。

## 离线回归验证

`python -m unittest tests.test_audit_recovery_run -v`：17 项测试通过，仅使用临时合成产物，无模型请求、不读取或修改真实实验输出。覆盖敏感字段哨兵、配置字段数值校验、完整路径、空轨迹、空 trace、未完成 optimizer、缺失训练/最终结果、源码不一致、可证明/不可证明的 eval 复用、重复行与 canonical token 去重、部分用量、非法分数及截断 JSON。配置字段中的模拟凭据字符串也不会进入 expected 或 Markdown。
