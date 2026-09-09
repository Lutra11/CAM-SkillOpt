# Windows 实验机只读诊断

采样时间：2026-09-09 14:04:40（UTC+08），回溯 14 天。来源为本机系统事件、Windows Update Operational 事件、服务和注册表只读查询。未修改系统设置、服务、更新策略、电源或事件日志。

## 已观测事实

| 检查项 | 观测 | 可以支持的结论 |
|---|---|---|
| System / EventLog 6008 | 09-08 18:37:40、09-09 00:43:58 各有一条 | 两次记录到非预期关机提示；这里是日志写入时间，不能当作实际关机发生时间 |
| System / EventLog 6005 | 与上述两个时间点相近 | 事件日志服务启动记录 |
| System / User32 1074 | 09-09 00:42:35，process_basename=wininit.exe，reason_code=0x50006 | 有进程发起的关机/重启事件；本次未据此判定自动更新或硬件原因 |
| 其他 System 重启记录 | 08-27 01:02:41，User32 1074，StartMenuExperienceHost.exe，0x0 | 另一次历史关机/重启发起记录 |
| System 41、6006、1001 | 本次筛选的 14 天可读事件内未返回 | 仅表示当前查询无匹配项，不能排除重启或日志缺失 |
| OS LastBootUpTime | CIM 访问被拒绝，0x80041003 | 未取得该字段；不是“系统正常” |
| 温度 | ACPI thermal WMI 查询访问被拒绝，0x80041003 | 当前温度不可用，也没有历史温度证据；不能声称正常或过热 |
| 更新相关服务 | UsoSvc Running / Automatic；wuauserv Stopped / Manual；BITS Stopped / Manual | 仅为采样时的服务状态，不足以判断过去的重启原因 |
| 自动更新 AU 策略键 | 检查的策略键未显式配置 | 不能推断系统全局自动更新关闭 |
| 两个待重启注册表指示 | WindowsUpdate RebootRequired=false；CBS RebootPending=false | 采样时未发现这两个指示；非穷尽检查 |
| WindowsUpdateClient Operational | 返回最近 80 条，达到采样上限 | 更新事件仍在产生，历史窗口可能被截断；未读取出可直接关联重启的结论 |

WindowsUpdateClient 的事件 ID 41 属于更新日志提供程序，**不是** System / Kernel-Power 的事件 41；两者不能混为一谈。JSON 保存了各自日志来源。

## 与实验失败的关系

历史 raw trace 中的 401 / invalid_refresh_token 是已确定的认证故障证据。上面的系统事件是独立机器健康证据，当前不足以证明两者因果关系，也不足以把重启归因于温度、自动更新或 Codex。

机器连续性应在新运行开始和结束分别保存该脚本快照，并配合实验 run ID、启动时间、进程退出码和阶段完成标记核对。即使温度传感器仍不可用，也应如实记录 unavailable；不得把缺测转为正常。

## 产物

- `windows_health_snapshot.json`：经过筛选的只读快照，不含原始事件正文、用户名或计算机名。
- `../../../scripts/cam_windows_health.ps1`：可重复运行的采样脚本，默认回溯 14 天；服务/注册表/事件读取出错时写 unavailable。

权限不足的 CIM 项在本轮未申请提升，也未安装监控软件。已取得的事件与服务数据可以作为本次 P0 的初始机器健康记录。

## 2026-09-09 15:11 追加检查

`windows_health_after_network_retries.json` 为两次旧 CLI P0 网络中断后的只读快照。System 返回的重启/关机相关事件仍为同样 6 条，最新仍为当天 00:43:58；与 14:04 初始快照及 14:44 运行中快照相比，没有新记录。本次两个 P0 均有明确 network_error 摘要和可观测的进程退出，不是单凭“没有日志”推断结束。

温度和 LastBootUpTime 仍不可读取；更新服务及两个待重启指示未改变。现有证据不支持把本次断流归因于系统重启，但也不能补造温度或硬件正常的结论。新版 CLI P0 完成后仍需追加结束快照。
