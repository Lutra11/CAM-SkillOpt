# Stage 12 恢复任务 1–5：最终结果

日期：2026-09-09。结论：认证、故障终止、产物留痕、单样本及四样本 P0 链路均已完成验收。**这不是正式 CAM-Full 方法验证，也没有观察到本轮测试分数提升。** 正式消融尚未启动。

## 验收状态

| 项目 | 结果 |
|---|---|
| target / optimizer 认证 | 各连续 2 次最小请求通过；模型、provider、返回内容、退出码正确；无 401 |
| 401 快速终止 | 计划 4 个模拟任务，仅第 1 次请求即退出；退出码 2；0 次外部调用；分数为 null |
| 失败分类与产物 | 认证/网络/模型/超时与普通任务错误分离；保留 results、conversation、原始 trace、失败类型与阶段统计 |
| 单样本预检 | llm/code/exec=true；analyst=1；patch=1，含 2 edits |
| 四样本 P0 | 正常退出码 0；24 条任务评估全部完成；独立审计 core_probe_evidence_pass=true |
| 离线回归 | 59 项运行回归＋29 项审计测试，共 88 项通过 |
| Windows 连续性 | 开始/结束快照未见新增重启事件，进程正常结束；温度不可读取，不能推断硬件正常 |

认证与传输配置的恢复参考 [OpenAI 官方认证文档](https://learn.chatgpt.com/docs/auth) 和 [配置文档](https://learn.chatgpt.com/docs/config-file/config-reference)。只更改实验进程配置；没有修改全局代理、安装新代理或关闭 TLS 校验。

## P0 的全部评分阶段

运行目录：`SkillOpt/outputs/recovery_20260909/p0_newcli_retry_01`。

固定配置：train/selection/test 各 4，1 epoch / 1 step，seed=42，workers=analyst_workers=1，exec_timeout=420；target=`gpt-5.6-terra` / none，optimizer=`gpt-5.6-sol` / medium；Codex CLI 0.153.4，官方 HTTP 传输，现有回环入口 `http://127.0.0.1:7890`。源码 SHA256：`524ca3716b4457a47f09c5baeb496a4f254d6b3852a01ec7e2bd01a66d6cadad`。

| 阶段 | 完成 | Hard | Soft | 解释 |
|---|---:|---:|---:|---|
| 初始技能 selection | 4/4 | 0.25 | 0.25 | 候选比较的参照 |
| 训练 rollout | 4/4 | 0.00 | 0.00 | 正常执行、评分不匹配；不是 infra_error |
| patch 候选 selection | 4/4 | 0.75 | 0.75 | 候选未被 CAM Gate 接受 |
| 最终技能 selection | 4/4 | 0.50 | 0.50 | 初始技能＋空 Slow Update 占位，被独立 final-selection 分支提升为 best |
| 初始技能 test | 4/4 | 0.25 | 0.25 | 测试参照 |
| best 技能 test | 4/4 | 0.25 | 0.25 | 相对初始 test 差值为 0 |
| final 技能 test | 复用 | 0.25 | 0.25 | 与 best 技能哈希相同，复用 test_eval；没有新增独立样本 |

六次独立执行的阶段各有 4 条记录，共 24 条评估记录；不能把跨阶段重复评估当作 24 个独立测试样本。完整复用来源、非空 assistant 轨迹及原始调用记录均已核实。

这 4 个 test 样本在 baseline 与 best 下逐项结果相同，hard/soft 配对差值均为 0。精确枚举 256 个配对 bootstrap 重采样，诊断性 95% 百分位区间为 `[0, 0]`。这是小样本全零差值产生的退化区间，不能证明两种方法等效，也不能推广为总体置信结论。

## 机制确实运行了什么

- 反思：2 个实际调用组，2 组均产生非空 patch，组级产出率 2/2。合并调用 1 次，3 处修改全部应用到候选。
- Adaptive Budget：实际计算 1 次；从配置初值 4 选择预算 8。这不是两次实测预算之间的变化。
- CAM Gate：实际调用 1 次。候选相对初始 selection 的配对均值差为 0.50，95% bootstrap 差值区间 `[0, 1]`，n=4、10,000 次重采样；包含 0，结果为 `re_evaluate`，不是 accept。
- Memory：未产生拒绝写入；持久化检索尚未接入 trainer，调用/命中为未知，不能声称 Memory 已生效。

最终 best 的来源是 `slow_update_placeholder_epoch_01`，不是得分 0.75 的 patch 候选。所有正式方法解释必须保留这个区别。当前 best/test 不能作为“CAM 成功采用 patch 后的效果”。

## 故障、时间与调用用量

| 指标 | 观测 |
|---|---:|
| 终止性基础设施错误 | 0 |
| 认证 / 模型不可用 / 模型超时终止 | 各 0/27 个 CLI 模型请求 |
| 代码生成 / 执行失败 | 各 0/24 条任务评估记录 |
| 正常评分不匹配 | 16/24 条评估记录；仅为调用级诊断，不是独立样本错误率 |
| 已恢复的网络通知 | 4 条，分布在 3/24 个 target 请求；没有抹除或计成评分失败 |
| 实际模型调用 | target 24＋analyst 2＋merge 1＝27 个 CLI 模型请求；不等于底层传输尝试次数 |
| target 已报告 token | 327,838（输入 320,404；输出 7,434） |
| optimizer 已报告 token | 56,542（输入 54,436；输出 2,106） |
| 合计已报告 token | 384,380；其中缓存输入 86,528 已包含在输入中，不重复相加 |
| P0 外层完整耗时 | 1128.313 秒，约 18 分 48 秒 |
| trainer 自报耗时 | 979.2 秒，不含开始的 baseline selection；不能替代完整耗时 |
| 货币成本 | 未知；没有账单与适用价格证据，不将 token 换算为费用 |

用量取每次请求的一份 canonical raw trace，不使用会遗漏 target token、重复计算 optimizer 的旧 tracker 总计。成功重连也有 transport_warnings 记录；本轮通过不代表网络从未波动或未来运行已保证稳定。

## 下一步边界与证据

先处理 [正式实验前核查清单](FORMAL_EXPERIMENT_PREREQUISITES.md)：接入持久化 Memory 检索，确认最终 best 提升与 CAM Gate 的设计一致性，修正调用/token 计量。之后再单独验收新的 CAM-Full，并按同一协议开展公平矩阵和多 seed 实验。当前不启动四组正式消融，也不据本探针撰写性能优势结论。

- [逐任务、完整性、机制与用量审计](P0_FINAL_AUDIT.md)；[机器可读审计](P0_FINAL_AUDIT.json)
- [全过程及失败尝试记录](RECOVERY_PROGRESS.md)
- [历史结果有效性纠正](VALIDITY_CORRECTION.md)
- [Windows 诊断](WINDOWS_DIAGNOSTICS.md)

原始任务、工作簿、完整对话与 trace 留在本地原实验目录；GitHub 仅同步代码、测试及筛选后的指标/诊断产物。旧失败运行没有被覆盖、拼接或当作有效负结果。
