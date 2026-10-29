# CAM v2r1 机制与落盘证据

本表对应 `docs/experiments/CAM_V2_PLAN.md`，使用函数名定位，避免代码变更后行号失效。

## 配置与协议

`skillopt/config.py::_FLATTEN_MAP` 必须把以下点号键送到 trainer 对应的平坦键：

| 点号键 | 平坦键 |
| --- | --- |
| cam.enabled / bootstrap_gate | cam_enabled / cam_bootstrap_gate |
| cam.adaptive_budget / min_budget / max_budget | cam_adaptive_budget / cam_min_budget / cam_max_budget |
| cam.memory_enabled / memory_top_k / memory_inconclusive | cam_memory_enabled / cam_memory_top_k / cam_memory_inconclusive |
| cam.soft_accept / drift_patience / drift_tolerance | cam_soft_accept / cam_drift_patience / cam_drift_tolerance |

`verify_config.py` 只校验平坦配置；无法展平的覆盖必须失败。
`strict_cam = cam_enabled and cam_bootstrap_gate`。
`cam_gate_state.json.policy = paired_selection_all_updates_v2r1`。
选择签名包含 v2 参数与门控/记忆/守卫源码指纹；整项研究另外使用 `outputs/studies/cam_v2r1_qwen/freeze.json` 冻结源码、有效配置和数据/工作簿。

## 每项机制的直接证据

| 机制 | 产物与字段 | 可证明内容 |
| --- | --- | --- |
| 成对门控 | cam_gate_events.json；steps/step_*/cam_gate.json；slow_update/epoch_*/cam_gate.json；final_cam_gate.json | 每次比较的pair_ids、CI、candidate/current/best hash、action、promotion_authorized、origin |
| 软接受 | history.action=soft_accept；summary.total_soft_accepts；epoch_stats.soft_accepts | current获得候选，best没有因此获得晋升授权；不能当hard accept计数 |
| 独立best授权 | event.promotion_authorized；cam_gate_state.json.events；runtime_state.best_skill_path | 只有晋升证据才能改变best；soft_accept仅进入current恢复授权集合 |
| 漂移守卫 | history.drift_guard；辅助更新目录/drift_guard.json；runtime_state.cam_drift_guard；summary.cam_drift_guard | 初始best也保护；同一步多次辅助更新不重复累加；累计rollbacks不能逐步相加 |
| 自适应预算 | history.lr_control_mode / edit_budget；每步budget证据 | 预算策略实际执行；不能单靠这些字段声称性能贡献，须比较no_adaptive_budget |
| 持久记忆写入 | cam_rejected_memory.json.items；steps/*/memory_write.json | kind=rejected/inconclusive与有符号score_change；不确定不等于退步 |
| 检索与使用 | patches/**/memory_retrieval.json；history.persistent_memory；summary.persistent_memory | hit_count、injected_groups、injected_items、completed_injected_groups分别是不同环节；命中不等于实际完成使用 |
| epoch本地buffer | trajectory_digest.json；训练反思上下文 | no_memory只关持久库，buffer仍存在，且不确定条目必须标为inconclusive |
| provider调用与成本 | results.jsonl 的llm_ok（SB）；summary.token_summary；attempts.jsonl / cost_summary.json | 真实返回用量与进程attempt成本；不代表未返回token的完整计费 |

## 各臂预期

| 臂 | bootstrap事件/证书 | 持久记忆 | 预算 |
| --- | --- | --- | --- |
| baseline | 禁止 | 禁止 | 共享原调度器 |
| cam_full | 必须有可解析事件 | 开启；仅存在可写条目时落库 | CAM adaptive |
| cam_no_bootstrap | 禁止 | 开启 | CAM adaptive |
| cam_no_memory | 必须有可解析事件 | 禁止持久库/写入 | CAM adaptive |
| cam_no_adaptive_budget | 必须有可解析事件 | 开启 | 共享原调度器 |

记忆库缺失可能因为没有写入机会；不能为了满足验收伪造reject、hit或注入记录。
LMB/OfficeQA没有SB的llm_ok字段时，用量/agent字段应按适用口径审计，缺失不等于0次调用。

## 完成验收

以 `scripts/experiment_integrity.py::validate_cell` 为准：连续完整训练步、动作统计一致、测试逐题ID与汇总一致、臂产物符合开关。
正式SB每阶段要求280个唯一题目；小规模冒烟允许显式test_env_num子集，但所有阶段必须使用同样ID。
final==best时，只有技能内容一致才允许复用test_eval逐题证据。

`collect_matrix.py --matrix-root outputs/matrix_v2r1 --report reports/v2r1/matrix_summary.json`
与 `analyze_cam.py --matrix-root outputs/matrix_v2r1 --report-dir reports/v2r1` 可手工重建验收表/机制摘要；正式运行器会自动调用。

## 中断与恢复

目录锁禁止两个robust runner并发写同一cell。已完成的单元须通过完整验收后才跳过。
选评目录身份不匹配时，旧阶段移入 `outputs/retry_archive/` 保留；不删除证据。
证书、配置、源码或数据不一致必须拒绝恢复，不能用启发式清理绕过。
