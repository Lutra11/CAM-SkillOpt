# Stage 12 实验有效性恢复

日期：2026-09-09（Asia/Shanghai）。本轮范围：任务 1–5。正式矩阵与论文性能结论需在预检通过后另行推进。

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
| 离线回归测试 | 4 个新增 unittest 模块 | 47 个测试全部通过；同步后的 GitHub 工作目录也通过 |
| 单样本完整链路 | `SkillOpt/outputs/recovery_20260909/single_http_02` | 通过：llm/code/exec 均 true，conversation 完整，analyst calls=1，patch=1（2 edits）；样本评分 hard=0/soft=0，非基础设施失败；总耗时 100.3 秒 |
| 四样本 P0 第一轮 | `SkillOpt/outputs/recovery_20260909/p0_http_01` | 第 8 次 target 请求发生 HTTP 网络断流，266.7 秒中止；7 个任务已有结果；整轮 invalid_infra，分数 null，未进入反思/后续评估 |
| 四样本 P0 同配置重试 | `SkillOpt/outputs/recovery_20260909/p0_http_02` | 已启动，新目录、同模型/代码/配置；原轮证据保留 |

早期 `auth_check_01` 与 `auth_check_http_01` 在后台预连接告警处过早终止，仅作为调试证据，不是最终认证验收。

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

在实验源码目录使用原 `.venv/Scripts/python.exe`：

```powershell
.venv/Scripts/python.exe scripts/check_spreadsheet_infra.py --out-root outputs/recovery_validation/new_401_check
.venv/Scripts/python.exe scripts/cam_recovery.py auth-check --transport http --out outputs/recovery/new_auth
.venv/Scripts/python.exe scripts/cam_recovery.py single --transport http --auth-summary outputs/recovery/new_auth/recovery_summary.json --out outputs/recovery/new_single
.venv/Scripts/python.exe scripts/cam_recovery.py p0 --transport http --auth-summary outputs/recovery/new_auth/recovery_summary.json --single-summary outputs/recovery/new_single/recovery_summary.json --out outputs/recovery/new_p0
```

每条命令是独立步骤。前三道验收未通过时，后续模型实验会被门禁拒绝。401 模拟命令预期退出码为 2，以其 `acceptance.json` 判断模拟验收是否通过。其它机器可通过 `--codex-bin`、`--auth-home` 和 `--base-config` 指定安装及数据位置。

## Windows 与论文约束

系统事件、温度可读性、更新服务状态见 [Windows 诊断](WINDOWS_DIAGNOSTICS.md)。历史重启与本批认证错误分别记录；无证据时不推断重启由模型调用、过热或自动更新引起。

本轮按用户要求先完成任务 1–5，暂不展开正式消融。只有有效 patch、实际 CAM Gate、预算和 Memory 记录充分后，才能恢复正式矩阵。现阶段不生成把基础设施失败当方法性能的论文表或置信区间。
