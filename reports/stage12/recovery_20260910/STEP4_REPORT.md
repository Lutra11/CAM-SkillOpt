# 第 4 项：冻结代码 n=4 P0 前置门禁结果

2026-09-10。状态：**已执行，但未通过认证门禁；single 与 P0 均未启动。** 本轮已经停止，没有后台实验继续运行，也没有进入第 5 项。

## 实际结果

使用第 3 项冻结实现 `2154488`，CLI 0.153.4，原有 HTTP 路由与本机 `127.0.0.1:7890` 代理，没有修改模型、reasoning、超时或运行代码。最小认证只要求返回固定标记 `CAM_AUTH_OK`，没有启动 SpreadsheetBench 训练或评分。

| 请求 | 模型 / reasoning | 结果 | 耗时 | 原始轨迹中的网络重连 |
|---|---|---|---:|---:|
| target_1 | gpt-5.6-terra / none | marker、模型、provider、退出检查通过 | 37.422 秒 | 1 次，随后恢复 |
| target_2 | gpt-5.6-terra / none | infra_error / llm_timeout | 90.406 秒 | 4 次，未恢复到有效答复 |
| optimizer_1 / optimizer_2 | 原计划 gpt-5.6-sol / medium | 未启动 | — | — |

认证门禁总耗时 128.625 秒。冻结的 `auth-check` 对每个固定标记请求设置 90 秒 deadline；P0 配置中的 420 秒没有被改动，也没有放宽认证 deadline。第二次请求没有收到标记或生成 last-message 文件，非零退出后停止整轮。

**没有发现 401、invalid_refresh_token 或模型不可用错误。** 原始日志直接记录 `stream connection failed` / `error sending request`，以及随后等待网络恢复。代理端口在失败后仍由同一 FlClashCore 进程监听，但这不能证明代理上游、外部网络路径或服务端哪一环节是根因。

## 本次暴露的格式识别缺口

两次认证的非 JSON stderr 都包含：

```text
ERROR: Reconnecting... waiting for network
```

冻结代码能把这一行识别为 error-channel 文本，但重连匹配器要求行首直接是 `Reconnecting`；带 `ERROR:` 前缀时不匹配，通用错误分类器也不识别单独的 `waiting for network`。因此：

- target_1 的 1 次已恢复通知未被结构化统计；target_2 的 4 次通知也未被统计。
- 两份 `transport_warnings.json` 的 `notice_count` 都是 0，台账的恢复通知计数也为 0，与原始轨迹的 **1 + 4** 不一致。
- 第二次请求没有在第三条重连通知处终止，而由仍有效的 90 秒总 deadline 终止，最终原始分类为 `llm_timeout`。

已用本地纯函数离线复现此格式：error-channel 识别为 true、reconnect notice 识别为 false、即时 infra 分类为 null。没有为此再调用模型。原始状态和台账未被改写；[STEP4_ACCEPTANCE.json](step4_auth_01/STEP4_ACCEPTANCE.json) 另存审计解释和原始/结构化计数差异。

第 3 项的 220 项离线通过记录保留不变；这次真实预检暴露了此前未覆盖的非 JSON 日志格式，不能用既有测试通过替代本次失败证据。

## 计量与 Windows 连续性

逐请求账本验证通过：两个独立 CLI invocation，各自原始 trace 的 SHA256 与记录一致；没有将重连通知当成新增调用。

认证脚本使用非 JSON 输出以核对 CLI 的 model/provider header，缺少可完整解析的 input/output usage。因此两次请求的 token 字段均保留 `null/unknown`，**不能声称成本为 0，不能报告精确总 tokens**。台账中的 `known_tokens=0` 仅表示没有可确定的分项，不表示免费或未调用。记录见 [AUTH_USAGE_AUDIT.json](step4_auth_01/AUTH_USAGE_AUDIT.json)；重连统计缺口仍按上一节单独报告。

Windows 快照从 10:13:46 到 10:18:22：最后启动时间均为 `2026-09-09 00:43:44.5 +08:00`，没有新增重启/异常关机事件；本轮实验 Python/Codex 进程已退出。开始和结束均存在待重启标记，温度读取不可用；没有改动 Windows 更新、服务或电源设置，也不据此推断过热。见 [开始快照](step4_auth_01/health_before.json)、[结束快照](step4_auth_01/health_after.json)。

冻结身份前后保持一致：

- 运行源码 SHA256：`b5dc39c846cedab48c0486a3d6b462eabfbafe80a7760c86b35020d31f1b0f8a`。
- 基础配置 SHA256：`f3374b0ad13a6fac347655bf012b92069601553b62fb92f528b95f8f55eb1fb9`。
- 本机完整产物：`C:/CAM-SkillOpt/SkillOpt/outputs/recovery_20260910_step4_01/`。原始轨迹、conversation 和认证目录只留本机；Git 仅同步本报告及白名单审计/健康元数据。

## 放行结论与下一步

**第 4 项未通过。** 不能使用第一次单独成功的请求跳过“每个角色连续两次”的门禁；single、n=4 P0、Memory 激活探针和扩大实验全部保持未启动，没有产生新的 benchmark 成绩。

按用户计划的代码冻结与硬停止要求，本轮暂停。建议确认后先修复非 JSON `ERROR:` 前缀重连识别，补上“成功恢复”和“第三条通知快速终止”的离线回归，重新冻结源码；再在全新目录从四次认证门禁开始。识别修复只改善分类和提前停止，**不能承诺它会修复真实网络不稳定**，后续仍须实际通过完整认证与单样本门禁才能运行 P0。
