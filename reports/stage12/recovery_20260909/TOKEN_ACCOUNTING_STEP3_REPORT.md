# 第 3 项：逐请求调用与 Token 计量修复

2026-09-10。状态：**代码修复与离线验收完成；本轮在第 3 项结束处暂停。**

本轮执行用户《第 1–6 项执行方案》的第 3 项。没有调用外部模型，没有启动新版 n=4 P0、Memory 探针、扩大 selection 或正式消融。历史 P0 只作为计量回放样本，不用新实现追认旧 CAM Gate 或论文性能结论。

## 修复范围与统计口径

按实现—方法一致性要求，本次同时修复 target 漏记、共享 tracker 重复汇总和逐请求溯源，保留未知值，不用“除以重复倍数”修补历史总数。

- `run_cli_failfast` 在 CLI 调用边界创建唯一 request ID；完成、认证失败、网络错误、超时和空响应均保存对应状态。每次重试是新 invocation；同一次 invocation 的 trace 副本及恢复通知不新增调用。
- 每个请求在 `token_accounting/requests/<request_id>/` 保存 `request.json` 和脱敏 `raw_trace.txt`。记录开始/结束时间、role、stage、backend、model、状态、失败类型、token 分项、完整性与 trace SHA256。跨进程独立文件和每请求锁避免共享 JSONL 追加竞争；结束记录不能被不同证据静默覆盖。
- target 不再写入 `(0, 0)`；optimizer 外层解析不再二次记账。Codex/Claude 共享 tracker 按对象身份只汇总一次；内存记录与持久账本再按 request ID 去重。
- `total_tokens = input_tokens + output_tokens`。cached input、reasoning output 和 cache-write input 单独展示，不重复加到总量。缺字段保持 `null`，`known_tokens` 仅是已观测下界，不是补全估计。
- `usage_complete` 检查 input/output 是否完整且终止事件身份没有歧义；缓存覆盖另用 `cache_usage_complete`。稳定 turn/event ID 可以去重；没有 ID 的完全相同终止事件不能武断认定为一次或两次，精确总量标为未知。
- trainer 的 `summary.json` 保存账本路径、请求 IDs、记录和摘要哈希。`step_usage.json` 按请求 ID 集合差记当前 step attempt，`usage_attempts/` 保留中止/重跑历史；不直接相减可能为 `null` 的累计 tokens。
- 训练入口隔离当前运行账本并重置进程 tracker，恢复时不删除磁盘账本；SDK、其他 legacy backend 和历史缺失证据均明确标为 partial，不能因后来改用 CLI 就追认完整。

这里的“调用”是**可观测的 Codex CLI invocation**，不是不可见的底层 HTTP 请求数，也不是账单笔数。CLI 内部重连、模型工具循环和缓存不会被猜测成额外计费请求；本报告不推算美元费用。已完成 CLI 响应也不代表生成代码、执行或评分必然成功，后者仍由任务结果单独分类。

解析字段与 [OpenAI 官方非交互模式文档](https://learn.chatgpt.com/docs/non-interactive-mode) 中的 `turn.completed.usage` 对齐；本轮模型、reasoning、数据、Gate、Adaptive Budget 和 Memory 选择规则均未更改。

## 历史 P0 的独立核对

只读来源：`C:/CAM-SkillOpt/SkillOpt/outputs/recovery_20260909/p0_newcli_retry_01/`。

| 来源 | CLI 调用 | Input | Output | Total | Cached input |
|---|---:|---:|---:|---:|---:|
| Target | 24 | 320,404 | 7,434 | 327,838 | 86,528 |
| Optimizer（analyst 2 + merge 1） | 3 | 54,436 | 2,106 | 56,542 | 0 |
| 合计 | 27 | 374,840 | 9,540 | 384,380 | 86,528 |

独立直接解析与新 parser、ledger、tracker、顶层 model summary **逐项一致**。顶层汇总同时加载共享 tracker 和同一批磁盘记录，仍为 27 次，没有重复计数。

原目录含 75 份带 usage 的 raw 文件，其中 48 份是 target trace 的逐字节副本；只选择 27 份 canonical evidence。若把所有副本直接相加，会得到错误的 1,040,056 tokens。27 个 thread ID 均不同，但 thread ID 只是关联标识，不冒充 provider billing request ID。同一个任务在不同阶段的执行必须分别计数。

该历史 run 有 4 条恢复通知，涉及 3 次 target 调用，终止性基础设施失败为 0。reasoning output 共 799，cache-write input 为 0，均已包含在父级分项中。历史方法有效性限制保持原状，见 [P0_FINAL_REPORT.md](P0_FINAL_REPORT.md)。

## 离线验收与冻结

**220/220 项通过，0 failure、0 error、0 skip**：新增 55 项（会计核心 34、CLI 边界 12、Trainer 集成 9）＋既有 165 项回归。测试耗时 24.298 秒，含历史文件校验和回放的完整验收耗时 28.045 秒。以上是离线验收耗时，不是模型训练耗时。

验收覆盖实际本地 Python 模拟子进程、401 快速退出、超时、空响应、网络恢复、解析重试、多个 worker 写同一账本、缺失/歧义 usage、共享 tracker、两运行目录隔离、原始证据篡改拒绝，以及 step 中断/恢复；**外部模型调用为 0**。开发时的多进程测试发现 Windows 首次创建目录的路径解析竞争，修正后纳入 34 项核心回归；没有因此启动或重跑真实实验。

最终证据：

- [acceptance.json](token_step3_offline/acceptance.json)：机器验收结果与源码逐文件哈希。
- [test_output.txt](token_step3_offline/test_output.txt)：220 项测试的 ID 与状态。
- [requests.jsonl](token_step3_offline/requests.jsonl)：27 条白名单计量记录。
- 本地完整产物：`C:/CAM-SkillOpt/SkillOpt/outputs/recovery_20260909/token_step3_offline_01/`，其中 `private_ledger/` 留在本机，不同步 Git。

历史目录的 **507 个文件全部未变**；前后文件树 SHA256 均为 `d36b26594c7edce3bcd2463fedb752cf5947ec03d2ac56840589ee243b50fa62`。历史源码 SHA256 仍保留为 `524ca3716b4457a47f09c5baeb496a4f254d6b3852a01ec7e2bd01a66d6cadad`，未替换为新源码标识。

实现提交：[`2154488`](https://github.com/Lutra11/CAM-SkillOpt/commit/2154488ff7ce117b18eb1502973318aaa37ffc5f)。冻结的运行源码 SHA256（`scripts.cam_recovery.source_hash()`）为 **`b5dc39c846cedab48c0486a3d6b462eabfbafe80a7760c86b35020d31f1b0f8a`**；实验目录与 Git 镜像一致。独立验收代码清单哈希为 `a159f717bc187d244966b4d57f77d3b5572ff8c119dca548f9dde2188ec0b7df`，验收前后相同。两种哈希的文件清单定义不同，不应互相替代。

可机器读取的冻结记录：[STEP3_FREEZE.json](token_step3_offline/STEP3_FREEZE.json)。文件 SHA256 对应本机原始字节；Git 对文本执行 CRLF/LF 标准化后，下载文件的字节哈希可能不同。验收产物的这些原始哈希应在上述本地输出目录核对，不把换行标准化误判为实验重跑。

验收程序 `scripts/check_token_accounting.py` 要求全新输出目录，拒绝写入历史 run；运行前后对历史全部文件和本次源码分别取哈希。公开 `requests.jsonl` 仅导出白名单计量元数据，原始 prompt/trace、工作簿和认证数据不进入 Git。

完整复现（在 Git 镜像根目录，输出必须不存在）：

```powershell
& C:/CAM-SkillOpt/SkillOpt/.venv/Scripts/python.exe scripts/check_token_accounting.py `
  --run C:/CAM-SkillOpt/SkillOpt/outputs/recovery_20260909/p0_newcli_retry_01 `
  --out C:/CAM-SkillOpt/SkillOpt/outputs/recovery_20260909/token_step3_recheck_new `
  --include-regressions
```

`--include-regressions` 包含镜像中的历史审计测试；运行源码目录没有该镜像专用模块时，可不带此参数运行 55 项计量专项与相同历史回放。本轮完整 220 项证据是在镜像目录产生的，运行文件与实验目录逐文件校验一致。

## 下一步边界

第 3 项验收通过后暂停，等待确认进入第 4 项。第 4 项须在冻结代码上重新认证验收并运行相同 n=4 P0，不能用本次历史回放替代新实验。之后仍须在 n=4 范围内观察自然 Memory 写入、后续命中与真实提示注入，才可考虑 n=8/16 和正式消融。
