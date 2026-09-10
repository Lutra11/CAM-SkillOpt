# 第 4 项 fix1：重连识别修复、重新冻结与门禁重跑

2026-09-10。最终状态：**修复与离线验收完成；四次认证与单样本通过；P0 在测试阶段因网络错误中止，第 4 项未通过。** 整轮 `invalid_infra`、`eligible_for_paper=false`。本轮已停止，没有进入第 5 项或自动重跑。

## 修复与冻结

用户批准后，仅修改 `skillopt/model/infra_errors.py` 的非 JSON stderr 重连识别：对锚定的 `ERROR: Reconnecting` 前缀建立匹配副本，证据仍保存原始消息。未泛化普通 stdout、assistant 文本、FATAL 或后台诊断日志。401、模型不可用、终止性 `turn.failed` 的优先中止逻辑不变；仍允许最多两条网络恢复通知，第三条中止，原 deadline 不变。

实现提交：`d01bf2bfa5932874ddcc6b479879bd96ec9020b4`。模型、reasoning、split、Gate、Memory、Budget、并发、规模和超时均未更改。修复提高识别与快速终止可靠性，不等于修复网络本身。

- 新源码 SHA256：`32a87a5303158b73e3dcdb8779ced7d5f6e9d37b4bf31f82ed2c25fc873d78ba`。
- 原配置 SHA256：`f3374b0ad13a6fac347655bf012b92069601553b62fb92f528b95f8f55eb1fb9`。
- 实验目录与 Git 镜像的 99 个运行源文件及 1 个新增测试文件逐字节一致。
- [STEP4_FREEZE.json](step4_fix1/STEP4_FREEZE.json) 记录版本、文件哈希、测试范围和固定参数。Git 换行标准化后的文本字节哈希可能不同，运行身份以本机冻结原始字节为准。

## 离线验收

**实验相关 236/236 项通过，0 failure、0 error、0 skip**，耗时 18.125 秒；其中既有 220 项加新增 16 项。新增测试覆盖真实前缀保留、1/2 条恢复、第三条快速中止、原 deadline、401/模型不可用/turn.failed 优先级，以及恢复不重复计调用和 tokens。均为本地模拟子进程，不调用模型。

新解析器只读回放上轮 raw，识别到 target_1 的 1 条与 target_2 的 4 条通知；没有修改上轮原始 `llm_timeout`、计数或报告。旧证据见 [第 4 项首次门禁报告](STEP4_REPORT.md)。

不能宣称“全仓所有测试通过”：初次在实验目录扩大 unittest discovery 得到 285 项，3 failure、7 error、2 skip，涉及基准依赖缺失和 Windows 路径断言。随后在同一 Git 镜像、同一环境做修改前后宽范围对照：239 → 255 项，两边均 4 failure、12 error、1 skip，非通过测试 ID 与异常类清单完全一致；新增 16 项没有引入新失败。镜像额外存在插件文件/依赖缺失，宽范围统计与实验目录不是同一个集合，不能混用。

证据：[实验相关验收](step4_fix1/offline/scoped/acceptance.json)、[236 项逐项结果](step4_fix1/offline/scoped/test_output.txt)、[修改前宽范围检查](step4_fix1/offline/baseline_broad/acceptance.json)、[修改后宽范围检查](step4_fix1/offline/fixed_broad/acceptance.json)。无关基准组件不属于此次最小修复范围，未安装额外依赖或扩展改动。

## 真实认证门禁

使用全新目录 `C:/CAM-SkillOpt/SkillOpt/outputs/recovery_20260910_step4_fix1_01/`。CLI 0.153.4，实验认证目录不变，HTTP 路由与 `127.0.0.1:7890` 不变。每次请求只返回固定标记，不用旧认证结果放行。

| 请求 | 模型 / reasoning | 结果 | 秒 | 重连通知 |
|---|---|---|---:|---:|
| target_1 | gpt-5.6-terra / none | 通过 | 25.344 | 0 |
| target_2 | gpt-5.6-terra / none | 通过 | 33.109 | 0 |
| optimizer_1 | gpt-5.6-sol / medium | 通过 | 27.234 | 0 |
| optimizer_2 | gpt-5.6-sol / medium | 通过 | 21.375 | 0 |

总耗时 107.859 秒。四次均模型/provider header、标记及退出检查通过，没有 401 或终止性网络错误。raw、transport sidecar、ledger 的重连计数均为 0；账本确认为 4 次 CLI invocation，trace SHA256 验证通过。

认证为非 JSON 输出，四次 token 均为 `null/unknown`，不是零成本。实验代码的 JSON 请求按 `turn.completed.usage` 另行审计，格式依据 [OpenAI 官方非交互说明](https://learn.chatgpt.com/docs/non-interactive-mode)。调用口径是可观测的 CLI invocation，不猜测 CLI 内部 HTTP 次数或账单笔数。

## 单样本与 P0

单样本在新的 `single/` 目录完成，使用刚通过的认证摘要，耗时 101.532 秒。训练样本 `47766` 的 `llm_ok/code_ok/exec_ok` 均 true，conversation 存在；hard=0、soft=0，属于 score_mismatch，不是基础设施失败。optimizer 确实调用 1 次，产出 1 个 patch、2 条编辑（insert_after、replace）。额外离线核对两条编辑分别实际应用，candidate skill 哈希不同于初始 skill；没有把仅非空的 edits 自动当作合法 patch。

单样本的两次 CLI invocation：target 12,346 tokens，analyst 17,605 tokens；合计 input 28,301 + output 1,650 = **29,951 tokens**，cached input 0。独立解析 raw 与账本、tracker summary 一致；这不包含四次未知用量的认证请求。见 [SINGLE_AUDIT.json](step4_fix1/SINGLE_AUDIT.json)。这是预检证据，不是 P0 成绩。

P0 固定 n=4、workers=1、analyst_workers=1、exec_timeout=420；新 run 从初始 skill 开始，没有将单样本候选人工注入。总耗时 **635.719 秒**（入口计时；Trainer 内部计时 634.709 秒）。已完成 1 个训练 step，随后在 baseline test 第 2 个样本 `41-47` 的模型请求处中止。

| 阶段 | 记录/计划样本 | 已评分 | hard / soft | 结论 |
|---|---:|---:|---|---|
| 初始 selection | 4/4 | 4 | 0.25 / 0.25 | 完成，仅作为中止运行的诊断证据 |
| 训练 rollout | 4/4 | 4 | 0 / 0 | 代码生成和执行均正常，全部 score_mismatch |
| 候选 selection | 4/4 | 4 | 0.50 / 0.50 | 完成；不能据此提升 best |
| 初始 test | 2/4 | 1 | null / null | 第 2 个请求 network_error，剩余 2 个未执行 |
| best/final test | 未启动 | 0 | null / null | 前置测试中止，不放行 |

初始 test 的首个样本 `52532` 已评分为 0；失败样本 `41-47` 的 hard/soft 为 null，未冒充普通 hard=0。P0 最终 summary 中整体和正式 selection/test 指标全部保持 null；上表已完成片段只供诊断，**不能当作完整实验成绩或方法优势**。逐样本白名单结果见 [P0_AUDIT.json](step4_fix1/P0_AUDIT.json)。

### 已完成 step 的机制证据

- 两组反思分别产出 2、1 条编辑；合并/排名保留 3 条 insert_after。独立在内存重新应用，candidate 全文与训练产物一致，哈希为 `a7e001dfbc01cff8e560321078fb519e27b3459ed9d3ae1946b56da644b8989e`。
- CAM Gate 实际触发 **1 次**。按相同 ID 配对，baseline `[1,0,0,0]`、candidate `[1,0,1,0]`，差值均值 0.25，95% 配对 bootstrap CI **[0, 0.75]**（seed 42、10,000 次）。独立重算与记录一致，判为 `cam_re_evaluate`，`promotion_authorized=false`、pending=true，不自动重采样或接受。
- current/best 与初始 skill 逐字相同，best_step=0；最终 `reuse_accepted_best` 使用初始 selection 缓存，**没有把候选 0.50 绕过 Gate 选成 best**。epoch 1 Slow Update 跳过空占位，没有改变 skill。
- Adaptive Budget 实际计算 **1 次**：失败类型计数集中于 1 类、共 4 项，entropy=0、confidence=1，min=2/max=8 得到 budget=8；step 记录 `lr_control_mode=cam_adaptive`。控制台仍打印基础配置 `lr_control=fixed`，不能用这一标签否定或替代实际计算记录。
- Persistent Memory 检索 **2 次**，两组均 `empty_history`、stored/eligible/hit=0；写入、命中、提示注入均为 0。请求元数据与真实 optimizer prompt 一致，埋点覆盖完整。此次是 pending 而非 reject，未产生自然拒绝记录；没有预置历史、人工写入或放宽 Gate。**Memory 尚未真实激活，不能扩大规模。**

### 网络快速终止与计量

失败请求的三条 JSON stdout 重连通知分别出现在 13.703、23.718、38.718 秒，进程在 **39.031 秒**终止，分类为 `network_error`，未等待 420 秒 deadline。此次真实失败走的是 JSON 通知路径；新增非 JSON `ERROR:` 前缀路径由离线回归和上轮 raw 回放验收，不能混称本轮真实失败就是该前缀。

另有 3 条成功恢复通知（analyst 2、target 1），与失败请求的 3 条未恢复通知分别统计。重连不新增调用；失败原始 trace、错误摘要和 conversation.json 文件均保留，但该失败请求的 conversation 为零条消息、没有 assistant 回复，不冒充完整对话。未发现 401 或 invalid_refresh_token。代理端口仍由同一 PID 3392 监听，但仅凭监听状态无法定位代理上游、网络路径或服务端哪一环节失稳。

| 范围 | CLI 调用 | 已知 tokens | 精确总 tokens |
|---|---:|---:|---|
| auth | 4 | 无完整 usage | unknown |
| single | 2 | 29,951 | 29,951 |
| P0 | 17（target 14、analyst 2、merge 1） | 237,617 | unknown：1 个失败请求缺 usage |

P0 已知 input 231,933 + output 5,684 = 237,617，已知 cached input 12,672 为 input 子集，不重复加入。17 次中 16 个成功、1 个 infra_error、0 个 running；13 个 target 任务实际完成代码执行，未记录代码或执行失败。已完成 step 内 11 次调用、168,200 tokens 按 request ID 差集单独核对，不能与总账本再相加。

raw → ledger → tracker/summary 及 step 统计核对一致，records/summary SHA256 通过验证。**计量可追溯不代表用量完整**：缺失字段保持 null，237,617 仅为 P0 已观测下界，不是精确成本，不推算美元费用。

## Windows 与产物边界

开始快照 10:46:51、认证结束/单样本开始前快照 10:53:05、单样本结束/P0 开始前快照 11:03:25、结束快照 11:17:40 的启动时间一致（2026-09-09 00:43:44.5 +08:00），5 条系统事件清单相同，无新增重启证据。11:18:42 进程检查确认没有本轮实验 Python/Codex 进程。待重启标记仍为 true，温度读取不可用；未改变系统更新、服务、电源或代理设置。

原始模型 trace、conversation、工作簿及认证文件只留本机。Git 仅同步代码、离线安全测试元数据和白名单验收/健康摘要。运行期间不再修改冻结的实验源码或配置。

## 放行决定

第 4 项 **not_passed**：认证与单样本通过，Gate/Budget/计量和 best 防绕过已得到真实证据，但 P0 有终止性网络错误、test 不完整，不能进入第 5 项。保持本轮 `invalid_infra` 和旧轮原始记录不变。

机器可读结论：[STEP4_ACCEPTANCE.json](step4_fix1/STEP4_ACCEPTANCE.json)；独立交叉检查：[INDEPENDENT_AUDIT.json](step4_fix1/INDEPENDENT_AUDIT.json)。

下一步应先排查当前已配置代理的上游与网络稳定性，再经确认决定新的第 4 项重跑；不自动延长 timeout、不放宽重连阈值、不补零、不用片段替代完整 P0，也不启动正式消融。
