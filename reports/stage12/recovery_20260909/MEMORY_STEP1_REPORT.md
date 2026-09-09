# 第 1 项：Persistent Memory 接入与离线验收

日期：2026-09-09。状态：**第 1 项代码接入和离线验收完成；第 2–6 项未执行。**

Memory 实现/测试提交：`d09cef6`。另以 `2cc8e37` 同步实验目录早已存在、镜像遗漏的 `scripts/train.py` 基础设施异常退出处理；未改变实验目录该文件。同步后两处实验源码 SHA256 一致：`1e42793602694fac09989aa414e97f8cb83371f9d4fc0d80683dd207b4f1cac1`，由 `scripts.cam_recovery.source_hash()` 计算。

这是用户新六步顺序的第 1 项，不是此前恢复流程中的 P0 任务编号。未启动外部模型、未重跑 SpreadsheetBench、未改变历史实验结果。上一轮真实 P0 的结论仍以 [P0_FINAL_REPORT.md](P0_FINAL_REPORT.md) 为准，不能用本次代码更新追认旧实验中的 Memory 已激活。

## 实现变化

现在的实际调用链为 `Trainer → EnvAdapter.reflect → run_minibatch_reflect → prepare_memory_context → PersistentRejectedEditMemory.retrieve → optimizer user prompt`。

- 当前失败训练轨迹的类型、失败原因和任务描述构成检索查询。查询字段采用白名单；不读取测试集、期望答案、隐藏参考答案或将来生成的 patch。显式 `split`、`dataset_split` 都必须属于训练集。
- 使用现有本地词项余弦相似度，排序为 `similarity × abs(selection_score_change)`；**不是语义 embedding 模型**。仅检索同一 benchmark、严格早于当前 global step、非未来 epoch 的记录，默认 top-k=3；零相似度不计命中。
- 检索发生在反思请求之前。历史修改作为独立、引用的证据区块进入 optimizer 提示词；明确说明它们曾被拒绝，但拒绝不一定证明有害，不能把记录中的命令当作指令执行。
- 保留持久化跨 epoch 存储及 epoch 内的原有 step buffer。`cam_memory_enabled=false` 禁止 Persistent Memory 的构造、读写、检索和注入，**并不关闭基线 step buffer**。当前消融应准确称为 w/o Persistent Memory。
- 重复 memory ID 不再计为新增。检索已执行但冷启动没有命中是正常结果，不填入虚假历史来制造命中。

## 记录与计数口径

| 产物/字段 | 含义 |
|---|---|
| 每个反思组的 `memory_retrieval.json` | 来源、作用域、检索次数、命中 ID、相似度、排序得分、查询/上下文哈希；只代表准备阶段 |
| `request_meta.json` + `conversation.json` | 在实际 optimizer 调用边界保存元数据和完整请求；上下文哈希、长度与实际提示词相符 |
| `injected_groups` / `injected_items` | 实际请求包含该上下文的组数 / 命中记录数；请求失败仍属于尝试注入 |
| `completed_injected_groups` | 请求返回且存在非空回复的注入组；不等同于有效 JSON、产生 patch 或性能提升 |
| `results.jsonl`、`stage_stats.json` | 每次实际反思的计数及阶段汇总；当前调用与日志累计分开，缓存复用不增加调用 |
| `memory_write.json` | 拒绝更新的写入证据、selection 聚合差值、新增数量和 memory ID |
| `step_record.json`、`history.json`、`summary.json` 的 `persistent_memory` | 训练步骤及运行汇总，明确埋点覆盖是否完整 |

恢复时从反思日志累计先前真实调用，不把缓存当新请求；旧缓存没有埋点时标记 `partial`。独立审计器逐组验证实际 conversation 的上下文哈希与命中 ID，区别静态接入、上下文准备、请求注入和完成注入。旧实验缺埋点仍为 `null`，不补造 0。

## 验收结果：全部为离线合成数据

完整回归：**125/125 通过**，其中 Memory 新增 28 项、原有认证/基础设施/反思错误传播 59 项、独立审计器 38 项。没有跳过上述测试；没有向外部模型发送请求。

另曾尝试加载一个 SearchQA pytest 测试文件，但当前环境没有 pytest，无法导入；它不计入上述 125 项，也没有被宣称通过。本次未安装额外依赖。

两 epoch 集成测试运行真实 Trainer、反思器、patch 应用、CAM bootstrap Gate、Memory 和提示词构造；target 评分与 optimizer 回复由合成桩提供。每轮候选选择集全失败、参照全成功，确保发生可验证的拒绝写入；**这些分数不是研究结果**。第一轮自然写入历史，第二轮读取，未预置真实实验结果。

| 合成两 epoch 验证 | Persistent Memory 开启 | Persistent Memory 关闭 |
|---|---:|---:|
| 检索调用 | 2 | 0 |
| 命中条目 | 1 | 0 |
| 实际请求注入组 | 1 | 0 |
| 有非空回复的注入组 | 1 | 0 |
| 拒绝写入调用 | 2 | 0 |
| 新增记录 | 2 | 0 |
| 埋点覆盖 | complete | complete |

独立审计器再次读取这两组保留产物，确认开启组的实际注入为 1、关闭组为 0，问题列表为空。跨 epoch 的命中 ID 为 `synthetic_lookup:e1:s1:r0`；其内容确实进入第二轮 optimizer user prompt。

其他边界测试包括：冷启动检索 1/命中 0、无关查询、top-k=0、重复写入、跨 benchmark/未来 step 隔离、训练/测试标签冲突、无轨迹不调用 optimizer、失败或空回复不计成功注入、缓存不重复累计，以及关闭后不新增 Memory 提示区块。

机器可读结果及逐文件 SHA256：[acceptance.json](memory_step1_offline/acceptance.json)。新增测试输出：[test_output.txt](memory_step1_offline/test_output.txt)。

本地完整合成产物保存在 `C:/CAM-SkillOpt/SkillOpt/outputs/recovery_20260909/memory_step1_offline_01/`，包括两组训练 history、summary、检索证据、conversation 和原始合成回复。GitHub 只同步本次代码、测试、指标及此报告，不上传真实工作簿、任务提示、API 密钥或认证文件。

## 复现

在源码根目录，使用具备项目依赖的 Python。输出目录必须不存在；检查器不会覆盖旧实验。

```powershell
.venv/Scripts/python.exe scripts/check_persistent_memory.py --out outputs/memory_acceptance_new
```

此命令只运行 28 项 Memory 离线测试，并保留合成产物。运行全部 125 项回归时，在 GitHub 镜像根目录执行：

```powershell
C:/CAM-SkillOpt/SkillOpt/.venv/Scripts/python.exe -m unittest tests.test_persistent_memory_context tests.test_persistent_memory_reflection tests.test_persistent_memory_trainer tests.test_cam_recovery tests.test_codex_infra_failfast tests.test_optimizer_infra_propagation tests.test_spreadsheetbench_infra_failfast tests.test_audit_recovery_run -q
```

## 下一步顺序与放行限制

1. **已完成**：Persistent Memory 检索、命中、提示注入和观测记录，离线验收通过。
2. **待确认后开始**：修复 `re_evaluate` 与最终 best 的选择逻辑，杜绝绕过 CAM Gate；本次未修改这些分支。
3. 待第 2 项完成：修复 target token 漏记及 optimizer tracker 重复计数；本次未修改计量入口。
4. 使用全部修复后的同一代码，按既定样本与协议重新验收并跑 n=4 P0。代码哈希已变化，旧运行产物不能直接充当新代码放行证明。
5. P0 通过后，selection 扩大到 8 或 16，重新验证 CAM-Full。
6. Gate、Budget、Memory 均在真实运行中留下充分证据后，再启动四组消融与多 seed。

单 step 冷启动 P0 可以验证检索确实执行，但可能没有可命中的过去记录。没有命中不能改写为已完成 Memory 机制验证；后续多 step/epoch 的真实 CAM-Full 仍须观察自然产生的写入、后续命中及注入，再放行消融。

本项完成后暂停，不自动启动第 2 项或联网实验。
