# CAM v3：机制改进与小规模验证

日期：2026-10-18。用户已授权按门控、记忆、预算三个方向改进，并用小规模实验验证。

## 版本与适用范围

本次改进通过新配置 `configs/experiments/cam_v3_mechanisms.yaml` 显式开启。未提供新策略字段时，保留原 v2 的算法选择，用于后续对照。新代码和配置必须使用新的输出目录与 freeze，不能接着运行旧 v1/v2 的检查点。原始结果、失败现场和 freeze 均保留，不用新策略重解释旧结果。

新增配置是机制配置，不是已经完成的 SpreadsheetBench 实验结果，也不会自动改变 `run_continuous_adaptation_v1.py` 使用的配置。它关闭内置 held-out test，并开启与 test 无关的最终验证；正式的四格、120题测试需要新版本研究运行器及独立冻结。

## 1. 门控：受约束的软接受

- `cam.soft_accept_policy=noninferiority`：当成对 bootstrap 的结果不确定时，只有收益区间下界不低于 `-cam.noninferiority_margin` 才采用为 current。默认试验 margin 为0.05。
- 显著有害的候选依然拒绝；margin 只约束不确定候选的软接受，不用于放行已判定有害的修改。
- best 的晋升仍需独立的提升证据；软接受不授权 best 晋升。
- `cam.group_guard=true`：保留 Cell/Sheet 的逐题类型信息。两类均至少5个配对样本，且任一类观察到的平均退化不得超过0.10；缺失或冲突的类型信息会暂缓采用。晋升 best 时也对照 best 检查。
- 分组检查是预设的描述性保护，不是分组同时置信保证。bootstrap 区间也不提供反复复用验证集时的全流程错误率保证。

## 2. 记忆：按更新组合记录证据

- `cam.memory_policy=evidence_v3`：每个训练步的整批编辑只保存一条 update_bundle，防止把整批效果归因给其中每条编辑。
- 保存 accepted/rejected/inconclusive 类型、技能前后哈希、任务类型、验证分数变化和分数发生变化的验证任务ID。
- supporting_task_ids 的含义是配对验证中分数变化的题目，不是训练支持题，也不是单条编辑的因果证据。由其数量得到的权重仅为启发式覆盖权重。
- 当前训练失败用于构造检索查询；不读取 test、答案或未来步骤记录。写入的失败模式使用实际训练任务元数据，不把分析器的自由文本描述任意配到其他失败组。
- 具体任务类型检索排除其他具体类型；mixed/overall 保持显式兼容范围。相同编辑组合去重，默认相似度下限0.15、证据权重半衰期8步。零分差的不确定记录不会仅因分差为零而失去检索资格。
- v3 记忆依赖严格门控的逐题缓存；同时开启 v3 记忆和关闭 bootstrap 的组合会在训练前明确报错。后续若要开展该消融，必须先提供等价的对齐验证证据路径，不能用空支持ID代替。

## 3. 预算：控制不同编辑的数量

- `cam.budget_policy=evidence_v3`：按操作、目标、内容去重；同一明确目标上的冲突修改从候选中移除。
- 只有显式任务ID列表才参与支持计数，并与当前实际训练任务ID核对；模型自报的 support_count 不等于可核对的独立任务数量。
- 有充分任务ID支持时，预算受不同合格编辑数量及原上限约束；缺少来源时退回保守最小预算，而非按失败集中程度扩大预算。可用编辑少于最小预算时允许更少；没有合格编辑时为0，不调用排序模型。
- 最近候选出现负分差时可再缩小预算。所有决定记录在每步 evidence_budget.json。
- 目前分析器和合并器不保证产生任务ID来源字段，因此真实运行可能经常使用保守回退。任务ID在训练集中存在，也不自动证明模型声称的支持关系正确；后续要单独验证来源质量。

## 最终验证公平性

新增 `evaluation.final_selection=true`，使 baseline 最后一次 slow update 即使在 `eval_test=false` 时也可参与最终验证及 best 选择。CAM 继续使用原来的最终成对门控。两组均只使用验证集完成选择，不为此额外调用测试集。显式启用时验证证据失败会终止，不以缺失证据代替已验证结果。

## 离线验证

全部测试和小规模实验由 GPT-6 Luna 高推理子agent执行，使用：

```powershell
& 'E:\MiniConda\envs\skillopt\python.exe' -X utf8 -m unittest tests.test_cam_v3_trainer tests.test_cam_gate_trainer tests.test_cam_selection_guard tests.test_cam_v2 tests.test_cam_v3_gate tests.test_cam_v3_memory tests.test_cam_v3_budget
& 'E:\MiniConda\envs\skillopt\python.exe' -X utf8 scripts/validate_cam_v3_mechanisms.py
& 'E:\MiniConda\envs\skillopt\python.exe' -X utf8 scripts/validate_cam_v3_binary.py
```

证据位置：

- `reports/cam_v3_mechanisms/tests.log`：集成和兼容性测试日志。
- `reports/cam_v3_mechanisms/protocol.json`、`results.json`、`report.md`：连续数值分数的合成机制案例；不能称为 SpreadsheetBench 的 hard 分布。
- `reports/cam_v3_mechanisms/binary/`：固定40题、Cell/Sheet各20题的0/1分数补充，6个预定情景，每情景20个种子。记录参数、源代码哈希和全部决定。

合成实验在运行前写入对应协议，固定参数，不依据结果调阈值。实验脚本阻断网络和模型子进程；不读取 .env，不触发远端模型。结果只能验证机制行为，不能证明实际任务准确率提高、统计覆盖率正确或长期效果稳定。

### 本轮验证结果

最终集成与兼容性回归：125个测试用例全部通过。新 YAML 经配置验证器核对，115个键解析成功，所有覆盖字段均有效。代码审查还发现并修复了 memory 查询未检查行内 source_split 的漏洞；标为 test/valid_unseen 的输入会在检索前被拒绝。

两组合成实验各包含6类情景×20个种子，每个组合对照旧/新策略。0/1分数补充的关键观察：

| 固定情景 | 旧策略20次决定 | v3的20次决定 |
| --- | --- | --- |
| 两类均明显改善 | 全部晋升 best | 全部晋升 best |
| 两类均明显退化 | 全部拒绝 | 全部拒绝 |
| 40题中仅一题从对变错 | 全部软接受 | 全部暂缓 |
| 总分不变，但逐题互有得失且区间较宽 | 全部软接受 | 全部暂缓 |
| Cell提升0.50、Sheet下降0.15、总体提升0.175 | 9次晋升best、11次软接受 | 全部分组保护暂缓 |

因此当前改进的直接证据是更保守地限制不确定和分组退化的更新，同时保留明显改善。高不确定性的零均值候选也被暂缓，说明保护会牺牲探索；不能宣称无代价地提高效果。测试中的20个种子只是固定合成情景的生成/重采样种子，不是20次真实模型训练。

记忆案例中，同一组3条编辑由旧格式的3条记录变为v3的1条组合记录；具体 Cell 类型查询排除了 Sheet 干扰记录，并保留有证据支持的零分差记录。预算案例验证了重复去重、同目标冲突排除、无来源的保守回退及任务ID支持筛选。更改前的实验快照保存在报告目录的 initial_run/ 与 history/，最终结果中记录的源文件哈希已由子agent核对。

## 在线实验状态与下一步

2026-10-19 最终状态：四格于03:34（Asia/Shanghai）完成并通过最终离线验收，巡检 `cam-20` 已停用，不自动启动扩展。seed42 baseline/CAM 为68/64题，seed43 为65/70题（各120题）；平均准确率55.4167%/55.8333%，CAM 平均差仅+0.4167个百分点且两个种子方向相反。本轮没有提供明显稳定提升的证据，更不能在七次辅助生成缺失、原冻结遗漏提示词及仅两个训练种子的限制下声称干净的机制归因。详见 `reports/continuous_adaptation_v3/FINAL_SUMMARY.md`、`FINAL_ACCEPTANCE.md`、`INCIDENTS.md`。下文保留历次状态与发现的时间线。

2026-10-19 01:18 巡检及后续核查：CAM seed43 第2、3轮慢更新分别以 `no_content` 结束（428.1s、507.4s），均记录相同的三次尝试后 `NoneType` 异常。目前合计三次已确认的辅助指导缺失；主训练前三步的ID和门控仍通过核对，冻结配置未改，队列继续运行。结果只能按包含这些缺失的实际执行来解释，不能假定是全部慢更新成功的完整机制比较。证据与诊断见 `reports/continuous_adaptation_v3/INCIDENTS.md`。

2026-10-18 23:48 巡检告警：CAM seed42 第3轮慢更新在模型调用重试失败后记录为 `no_content`，没有生成该轮辅助指导；后续元技能和第4步调用继续成功。按冻结实现保留技能并继续，未补跑或回填。此缺失必须随最终结果披露，不能把验收通过等同于全部可选更新成功。详见 `reports/continuous_adaptation_v3/INCIDENTS.md` 及对应 incident JSON；失败根因尚未证实。

2026-10-18 20:42（Asia/Shanghai）已启动 `continuous_adaptation_v3`，首格为 baseline seed42，其余三格等待依次验收后运行。启动前141项组合测试、115键配置检查通过；两次准备保持 freeze 不变，1,378个源文件指纹验证通过，训练及测试ID与原精简方案完全一致。队列见 `outputs/studies/continuous_adaptation_v3/queue_state.json`，独立后台监督状态见 `reports/continuous_adaptation_v3/supervisor_state.json`，逐格结果报告见 `reports/continuous_adaptation_v3/report.md`。此段记录启动事件，实时完成情况以队列与报告为准。

后续推进时已完成限量安全认证检查：GLM optimizer 与 Qwen target 均用本机现有角色凭据返回HTTP 200，各只发送一次短请求，未修改密钥或服务。此前401未复现；记录见 `reports/cam_v3_online/auth/probe_report.json`。

新的 `continuous_adaptation_v3` 协议使用 baseline/v3 CAM 各种子42、43，沿用原固定A/B/A/B四步及120题测试ID，逐格验收、单lane依次执行。先完成seed42配对，再继续seed43配对；质量与实际token成本一起报告。此对照估计v3相对baseline的整体差异，没有同时运行v2组，不能据此声称真实任务上v3优于v2。只有完成真实运行后才能判断实际收益。

后续可以追加记忆或预算的单组件消融；每次只改变一个机制，独立登记输出与分析。暂不加入任务条件化技能模块，以便本轮变化可解释。
