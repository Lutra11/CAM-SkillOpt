# v2r1 审查与修复记录

## 决策

2026-10-18 用户选择方案2；随后要求测试和实验由 GPT-6 Luna high 子 agent 执行，最多三个子 agent 与主 agent 并行。
旧队列保留归档；Qwen 成为主目标，全部9格采用新冻结协议，历史 baseline 不进入正式比较。

## 修复范围

- config.py 显式映射 v2 的 soft_accept / drift_patience / drift_tolerance / memory_inconclusive。
- verify_config.py 校验 trainer 的实际平坦配置，未知字段拒绝，不回查原 YAML 产生假阳性。
- trainer 恢复允许 soft_accept 授权 current，仍单独核验 best 的晋升授权。
- 独立 DriftGuard 检查初始 best、持久化计数、同一global step去重、覆盖辅助更新；仅 strict CAM 应用；0关闭有效。
- neutral memory 带 kind 注入并保留有符号分差；epoch buffer也不把不确定称为失败。
- step与epoch分离计数soft accept。
- strict integrity验收连续步、动作统计、逐题ID与分数；显式小规模评测可取固定test的子集，但三阶段ID必须一致；正式280题要求全量一致。
- no_bootstrap 无bootstrap文件，no_memory 无持久库；LMB没有llm_ok字段记未知而非0次调用。
- robust runner 使用进程锁、正确透传out_root，错误选评阶段归档，证书失败不启发式删除；逐attempt记录成本覆盖。
- 配对统计拒绝默默取交集、重复ID和坏行，支持final==best的有证据复用。
- 正式运行器冻结源码、配置、数据和工作簿，按seed块执行并自动报告；主/次指标固定，消融Holm完整族才报告校正值。

## 离线验证

子 agent 执行相关组合 unittest：151项通过、0失败。报告工具测试另外6项通过。最终显式子集验收回归通过；test_experiment_integrity 共7项通过。独立只读检查未发现所查恢复、漂移、锁与验收路径的严重缺陷。九格配置差异检查通过。

全仓库一次 discovery 共288项，存在4个插件文件同步/缺失失败与12个导入错误，关联未安装的pytest、可选mcp_server、skillopt_sleep等；这不是全仓库通过。没有为此安装依赖或改动无关模块。

## 实机验证

Qwen CAM quick冒烟已开始；初始4道选评题完成，第一步已完成反思/合并/预算选择/技能编辑，候选选评进行中，暂未观察到infra错误。第一步真实门控已记录soft_accept=1、promotion_authorized=false；2条inconclusive记忆落库，分析器0告警。完整闭环结果与正式启动状态待回填。

正式协议已冻结1398个文件，包括全部1201个数据文件；freeze不包含.env。

凭据形态扫描覆盖362个文本文件（排除.env）；前缀/Bearer命中限于已存在的测试fixture。生产代码/configs/reports/v2r1/冒烟输出未发现这两类命中。宽泛赋值模式命中配置与CLI代码，未打印值。

## 结论边界

本轮是探索性验证，设计已受旧测试结果影响；三seed与单seed消融只能提供有限证据。不将机制触发当成性能贡献，不将题目CI当成训练方差，不取best/final测试最大值。
