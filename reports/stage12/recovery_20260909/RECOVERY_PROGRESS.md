# Stage 12 实验有效性恢复

日期：2026-09-09（Asia/Shanghai）。本轮范围：任务 1–5。正式矩阵与论文性能结论需在预检通过后另行推进。

**最新结论：恢复任务 1–5 已完成验收，P0 已正常结束并通过独立完整性审计。** 请先读 [最终中文报告](P0_FINAL_REPORT.md)。baseline/best/final test 均为 0.25，没有测试分数提升；持久化 Memory 与最终 best 选择流程仍需核查，正式消融未启动。

## 已确认的事实

- 已对实验专用 `C:/CAM-SkillOpt/codex-home` 完成官方设备登录；target 与 optimizer 使用同一认证环境。
- 历史 CAM-Full、三组消融及 P1/P2 的认证故障证据、论文排除标记见 [有效性纠正](VALIDITY_CORRECTION.md)。此前将正式全零解释为方法负结果的结论撤回。
- 修复基础设施错误吞掉链路。认证错误第一请求退出；网络/模型不可用/模型超时分别分类；中止摘要的分数为 `null`。
- 模型默认超时为 420 秒，防止 optimizer 未提供 timeout 时无限等待。子进程终止仅针对该请求启动的进程树。
- 本地表格解析/执行错误不按字符串误识别为认证错误；真实模型回答涉及“401”也不作为认证证据。
- 缺失真实 assistant 轨迹会触发 `artifact_missing`，不会静默进入 `skip_no_patches`。
- 任务产物保存 `results.jsonl`、`failure_type`、`conversation.json`、原始 trace、`stage_stats.json`；无响应时 conversation 是真实的空记录，并附状态元数据。反思阶段保存输入、响应、patch 与调用记录。
- 部分任务结果不能代表整批已完成；缓存中的已知认证失败会被拒绝复用。
- 修复 CLI 配置覆盖：传输、reasoning 和审批参数统一位于 `exec` 后，避免不同参数层级覆盖。

## 已运行的验收

| 检查 | 证据 | 结果 |
|---|---|---|
| 原默认传输认证 | `SkillOpt/outputs/recovery_20260909/auth_check_02` | target/optimizer 各连续 2 次正常返回，无 401；存在 WebSocket 回退 |
| 401 整链路模拟 | `SkillOpt/outputs/recovery_validation/simulated_401_first_request_v2` | 4 个计划任务仅发出 1 次模拟请求；0 次外部调用；真实退出码 2；`invalid_auth`、所有分数 null |
| 默认传输单样本 | `SkillOpt/outputs/recovery_20260909/single_01` | 实际网络断流，约 27.9 秒中止；无任务评分 |
| HTTP 独立诊断 | `SkillOpt/outputs/recovery_20260909/http_diagnostic_01` | 同一官方端点、同一 target 模型返回正确标记，provider 为 `cam_openai_http` |
| 参数覆盖诊断 | `auth_check_http_02` / `single_http_01` | manifest 虽标 HTTP，实际仍为 openai；不能作为 HTTP 验收。原产物保留，后续验收强制核对实际 provider |
| 修正后 HTTP 双角色验收 | `SkillOpt/outputs/recovery_20260909/auth_check_http_03` | 4/4 通过；模型、实际 provider、退出码和返回内容均正确；总耗时 117.1 秒 |
| 离线回归测试 | 4 个运行模块＋1 个审计模块 | 加入代理隔离与有界重连测试后 59 个运行回归测试通过；另有 29 项独立离线审计测试，共 88 项 |
| 单样本完整链路 | `SkillOpt/outputs/recovery_20260909/single_http_02` | 通过：llm/code/exec 均 true，conversation 完整，analyst calls=1，patch=1（2 edits）；样本评分 hard=0/soft=0，非基础设施失败；总耗时 100.3 秒 |
| 四样本 P0 第一轮 | `SkillOpt/outputs/recovery_20260909/p0_http_01` | 第 8 次 target 请求发生 HTTP 网络断流，266.7 秒中止；7 个任务已有结果；整轮 invalid_infra，分数 null，未进入反思/后续评估 |
| 四样本 P0 同配置重试 | `SkillOpt/outputs/recovery_20260909/p0_http_02` | 第 7 次 target 请求发生网络断流，243.0 秒中止；6 个任务完成评分；未进入反思，整轮 invalid_infra |
| 旧 CLI + 现有回环代理 | `SkillOpt/outputs/recovery_20260909/auth_check_proxy_01` | 首个最小请求 network_error，26.2 秒终止；不能把端点可达当成模型稳定 |
| 本机新版 CLI + 同一回环代理 | `SkillOpt/outputs/recovery_20260909/auth_check_newcli_proxy_01` | 4/4 通过，两个角色各连续 2 次，实际 provider/model 正确；138.0 秒；无 401 或断流 |
| 新版 CLI 单样本 | `SkillOpt/outputs/recovery_20260909/single_newcli_proxy_01` | 通过：llm/code/exec=true，conversation 完整，analyst=1，patch=1（2 edits）；hard/soft=0；77.0 秒 |
| 新版 CLI 四样本 P0 | `SkillOpt/outputs/recovery_20260909/p0_newcli_proxy_01` | 首个 selection 请求在“正在重连”通知处被上层终止；24.2 秒，0 个有效评分，整轮 invalid_infra；后续检查确认该通知尚非最终失败 |
| 有界重连修复后认证 | `SkillOpt/outputs/recovery_20260909/auth_check_newcli_retry_01` | 4/4 通过；双角色各连续 2 次，实际模型/provider 正确；155.4 秒 |
| 有界重连修复后单样本 | `SkillOpt/outputs/recovery_20260909/single_newcli_retry_01` | 完整链路通过；analyst=1，patch=1（2 edits）；hard/soft=0；105.7 秒 |
| 有界重连修复后 P0 | `SkillOpt/outputs/recovery_20260909/p0_newcli_retry_01` | 正常退出码 0，独立完整性审计通过；24 条评分、2 组 patch/3 edits、预算=8、Gate=1；baseline/best/final test 均 0.25；完整耗时 1128.313 秒 |

当前 P0 的阶段性事实：baseline selection 4/4，hard/soft=0.25；训练 4/4，hard/soft=0；候选 selection 4/4，hard/soft=0.75。候选与初始技能的 4 个配对差值均值为 0.5，95% bootstrap 差值区间为 `[0, 1]`，包含 0，因此 CAM Gate 输出 `re_evaluate`，候选没有被接受。此为 n=4 探针的运行轨迹，不是正式性能提升结论。实际反思请求为 2 次、合并请求为 1 次；原 trainer token_summary 存在已记录的重复计数限制，不能直接当调用成本。

最终阶段：初始技能＋空 Slow Update 占位的 selection 为 0.50，被独立 final-selection 分支提升为 best；其 test 为 0.25，与初始技能相同。final/best 哈希一致，final test 合法复用 best 的 4 条记录，不另增样本。本轮 27 个 CLI 模型请求的 canonical 已报告用量合计 384,380 tokens；3 个请求共 4 条网络通知恢复成功，所有通知保留，终止性基础设施错误为 0。核查清单见 [正式实验前提](FORMAL_EXPERIMENT_PREREQUISITES.md)。

早期 `auth_check_01` 与 `auth_check_http_01` 在后台预连接告警处过早终止，仅作为调试证据，不是最终认证验收。

## 连接恢复与实验边界

用户反馈没有在用的本地代理；只读检查发现后台已有 FlClashCore 监听 `127.0.0.1:7890`，但 WinHTTP 为 Direct、系统代理开关关闭，实验没有代理环境变量。未启动或修改该服务，也未修改全局代理、路由、认证或 TLS 校验。

不带凭据的端点连通性检查中，直连报 ConnectionError；通过现有回环入口获得 HTTP 405，只能证明当时 TLS/端点可达，不能证明模型请求成功。随后旧 CLI 最小请求仍断流。改用本机已安装的 Codex 0.153.4（原实验 CLI 为 0.147.0），重新进行 4 次模型验收和同一样本全链路，均通过。此观察不足以单独证明故障完全由旧版本造成。

新增 `--proxy-url` 仅允许无凭据的回环 HTTP 地址，只修改实验进程及其子进程环境；清除冲突的 ALL_PROXY/NO_PROXY，拒绝未显式记录的继承代理。实际地址、CLI 路径、模型/配置/源码哈希写入 manifest；换 CLI 或代理必须重新过门禁。没有安装新代理、下载源码、关闭证书验证或改用其他模型。

新版验收与单样本的运行源码 SHA256 均为 `cf58ca1ef6b2f831280f14dd469d835394d924190e012886154031f9c4d37689`；配置 SHA256 为 `f3374b0ad13a6fac347655bf012b92069601553b62fb92f528b95f8f55eb1fb9`。旧版两次 P0 的中断记录完整保留，不拼接为一轮成功实验。

新版 CLI 二进制 SHA256：`e5aa76d19c7c94e2e9ef9b707d590206a73ac0e97c8ddc8382181242494bef75`。本次路径为 `C:/Users/hp/AppData/Local/OpenAI/Codex/bin/8e5b6932251c2c1c/codex.exe`；未覆盖原实验 CLI。

15:15 的新 P0 原始事件是 `Reconnecting... waiting for network`，同时存在 `waiting to retry` / `retry_delay=5s` 警告，而不是 `turn.failed`。当时 fail-fast 将所有 JSON error 都当成最终失败，这会过早杀掉可恢复的网络等待。现已修正为：认证/模型不可用立即终止，最终失败立即终止；仅明确的网络重连通知允许最多 2 条恢复机会，第 3 条或原超时期限到达仍中止。未增加请求总期限，也未增加正式实验重试循环。

每个请求另存 `transport_warnings.json`，报告通知数量和是否恢复，不能将恢复后的连接称为“从未断流”。修复后的 401 整链路模拟再次通过（`simulated_401_bounded_reconnect`，1 次模拟请求、0 次外部调用、真实退出码 2）。运行回归 59 项通过，另有独立离线审计测试。新运行源码 SHA256 为 `524ca3716b4457a47f09c5baeb496a4f254d6b3852a01ec7e2bd01a66d6cadad`；旧中止结果不会被改写。

## 固定的恢复实验配置

配置来源：`configs/spreadsheetbench/cam_recovery_p0.json`，复制历史有效 P0 fix1 的参数，输出目录运行时重新指定。

| 参数 | 值 |
|---|---|
| target / optimizer | gpt-5.6-terra / gpt-5.6-sol |
| target / optimizer reasoning | none / medium |
| workers / analyst_workers | 1 / 1 |
| train / selection / test | 4 / 4 / 4 |
| epochs / steps | 1 / 1 |
| seed / split_seed | 42 / 42 |
| exec_timeout | 420 秒 |
| split / data | 现有 spreadsheetbench_split / spreadsheetbench_verified_400 |
| 最终候选传输 | 官方 OpenAI HTTP，必须通过实际 provider 验收 |

单样本预检固定取该 P0 首个训练 batch 的首个样本 `47766`。一次失败不会换用更容易的样本。每次尝试使用新目录，保存源码与配置哈希。

## 复现入口

在实验源码目录使用原 `.venv/Scripts/python.exe`。下例为本机最新验收使用的 CLI 与现有回环入口；换机器时必须先核实两者存在，不能把此地址当作新安装代理的指令：

```powershell
.venv/Scripts/python.exe scripts/check_spreadsheet_infra.py --out-root outputs/recovery_validation/new_401_check
$recoveryRoute = @('--transport', 'http', '--proxy-url', 'http://127.0.0.1:7890', '--codex-bin', 'C:/Users/hp/AppData/Local/OpenAI/Codex/bin/8e5b6932251c2c1c/codex.exe')
.venv/Scripts/python.exe scripts/cam_recovery.py auth-check @recoveryRoute --out outputs/recovery/new_auth
.venv/Scripts/python.exe scripts/cam_recovery.py single @recoveryRoute --auth-summary outputs/recovery/new_auth/recovery_summary.json --out outputs/recovery/new_single
.venv/Scripts/python.exe scripts/cam_recovery.py p0 @recoveryRoute --auth-summary outputs/recovery/new_auth/recovery_summary.json --single-summary outputs/recovery/new_single/recovery_summary.json --out outputs/recovery/new_p0
```

每条命令是独立步骤。前三道验收未通过时，后续模型实验会被门禁拒绝。401 模拟命令预期退出码为 2，以其 `acceptance.json` 判断模拟验收是否通过。其它机器可通过 `--codex-bin`、`--auth-home` 和 `--base-config` 指定安装及数据位置。

## Windows 与论文约束

系统事件、温度可读性、更新服务状态见 [Windows 诊断](WINDOWS_DIAGNOSTICS.md)。历史重启与本批认证错误分别记录；无证据时不推断重启由模型调用、过热或自动更新引起。

本轮按用户要求先完成任务 1–5，暂不展开正式消融。只有有效 patch、实际 CAM Gate、预算和 Memory 记录充分后，才能恢复正式矩阵。现阶段不生成把基础设施失败当方法性能的论文表或置信区间。
