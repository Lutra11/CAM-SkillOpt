# CAM 实验交接（v3 已完成，当前为 v3r1 离线修复）

> 2026-10-19 当前任务：用户选择方案 1，进行运行可靠性修复与已有机制诊断，修订为 `cam_v3r1_runtime_diagnostics`，模型请求预算为 0。协议见 [CAM_V3R1_RUNTIME_DIAGNOSTICS.md](docs/experiments/CAM_V3R1_RUNTIME_DIAGNOSTICS.md)。本修订仅允许离线 prepare/verify，没有运行队列；不重跑旧四格，也不自动扩展实验。测试和诊断交给 GPT-6 Luna high 子 agent。后续交接以该协议及新修订验收报告为准。

> 方案 1 已于 2026-10-19 15:58（Asia/Shanghai）完成：140 项唯一离线 unittest 通过，新冻结 1,445 文件含 21 份提示词，重复 prepare 与 verify 通过。旧 1,378 文件快照和 225 份修改前备份均保全，五份旧关键证据哈希不变。诊断显示预算 8/8 步来源未知、4 次类型重现中 3 次注入记忆；没有新准确率结果。详见 [REPAIR_REPORT.md](reports/cam_v3r1_runtime_diagnostics/REPAIR_REPORT.md)、[DIAGNOSIS.md](reports/cam_v3r1_runtime_diagnostics/DIAGNOSIS.md)、[freeze_acceptance.json](reports/cam_v3r1_runtime_diagnostics/freeze_acceptance.json) 和 [COMPLETION.json](reports/cam_v3r1_runtime_diagnostics/COMPLETION.json)。`cam-20` 保持 PAUSED。

> 2026-10-19 最新状态：`continuous_adaptation_v3` 四格于03:34（Asia/Shanghai）完成，最终离线验收通过；队列及监督器均为 complete、进程正常退出。seed42 baseline/CAM 为68/64题，seed43 为65/70题，均为相同120题；两个种子的平均差为 CAM +0.4167个百分点，方向不一致，不能视为明显稳定提升。最终结果见 [FINAL_SUMMARY.md](reports/continuous_adaptation_v3/FINAL_SUMMARY.md)、[FINAL_ACCEPTANCE.md](reports/continuous_adaptation_v3/FINAL_ACCEPTANCE.md)，必须同时阅读 [INCIDENTS.md](reports/continuous_adaptation_v3/INCIDENTS.md) 中七次辅助生成缺失及提示词冻结范围限制。巡检 `cam-20` 已停用，没有自动扩展实验。下文是历史 v2r1 状态，不是当前启动指令；勿据此重启旧矩阵。

## 以下为历史 v2r1 记录（禁止据此启动）

- 用户当时选择方案 2：先修复，再聚焦一个目标模型做成对 seeds 与关键消融。
- 后续路线已记录为计划 1、3、4，见 `docs/experiments/CAM_FUTURE_PLAN_134.md`；当前先执行计划 1（三个消融补 seed 43、44，共 6 格），再评估预算匹配计划 3 和独立泛化计划 4。
- 权威计划：`docs/experiments/CAM_V2_PLAN.md`。
- 旧 HANDOFF、计划、报告、源码和进程快照：`_archive/v2_pre_review_20261018/snapshot/`。

## 当前矩阵

Qwen target、GLM optimizer。baseline / cam_full 各 seed42、43、44；三个 seed42 消融 no_bootstrap、no_memory、no_adaptive_budget。共 9 个全新单元。
不复用旧 baseline 作为正式主比较，不运行其它模型/benchmark 扩展。

## 唯一运行入口

Python：`E:/MiniConda/envs/skillopt/python.exe`，必须 `-X utf8`。

```powershell
& "E:/MiniConda/envs/skillopt/python.exe" -X utf8 scripts/run_study.py --run --lanes 2
```

不带 --run 时只列计划。运行器使用目录锁，source/data freeze，按 paired seed blocks 调度；成功后自动做验收和报告。失败会停在 needs_attention。
**不要再启动旧的 run_phase_queue.py --phases b_rest,c,d,e 队列。**
正式运行冻结后，不得修改 skillopt/scripts/tests/configs 或数据；如需修复，保留旧证据并使用新修订命名空间。

## 状态与产物

- 实时队列：`reports/v2r1/queue_state.json`（正式启动后创建）。
- 队列日志与逐格日志：`reports/v2r1/logs/`。
- 单元目录：`outputs/matrix_v2r1/spreadsheetbench/qwen/<method>_seed<seed>/`。
- 冻结清单、配置与源码数据快照：`outputs/studies/cam_v2r1_qwen/`。
- 主报告：`reports/v2r1/EXPERIMENT_RESULTS.md`、`paired_results.json`。
- 验收表：`reports/v2r1/matrix_summary.json`、`matrix.md`。
- CAM 机制证据：`reports/v2r1/cam_analysis.json`。
- 每格 `attempts.jsonl` / `cost_summary.json` 汇总失败与成功进程的已返回 provider 用量；不包含不可见的计费 token。

## 已修问题

v2 参数展平、预检假阳性、soft_accept 恢复授权、初始 best 漂移保护、漂移计数持久化/同一步去重、soft 接受 epoch 汇总、neutral memory kind 注入、no_bootstrap 验收、LMB llm_ok 缺失误判、输出根目录透传、目录锁、选评错误阶段保留归档。

漂移守卫仅 strict CAM 启用；主指标 test_hard，次指标 final_test_hard，禁止挑较高者。
三 seed 用配对差值均值/SD 描述训练波动，题目 bootstrap CI 是另一层不确定性；单 seed 消融不支持稳定性强结论。

## 验收标准

8 个连续训练步；summary/history 动作计数一致；三份 test 都为同样的280题、无重复/缺失、汇总分数一致；CAM 门控证据可解析；无门控/无记忆臂产物符合开关。
summary 没有 status 字段可以是正常完成，但必须通过 scripts.experiment_integrity.validate_cell。
不要因为无 status 或队列旧字段而重跑。

## 当前执行状态

- 旧版 `run_phase_queue.py --phases b_rest,c,d,e --lanes 3` 曾被外部运行器重启两次；确切旧队列树均已停止，`reports/queue_state.json` 已标记为 superseded。不要重新启动旧队列，也不要使用会按命令行宽泛匹配并终止所有 Python 实验进程的清理命令。
- 首次 Qwen 冒烟 PID24528 在 step 3 反思阶段中断，原因是旧运行器的宽泛清理命令。runtime_state 保留 `last_completed_step=2`，step_0003 的 rollout、反思缓存也保留。恢复 wrapper PID30252 后，按用户选择先等待外部 GLM cam_full 单格验证结束，将 Qwen 停在可恢复状态；恢复时仍用同一冒烟目录和已冻结参数。
- 外部 GLM 验证在 baseline selection 37/40 遇到 provider 502（summary 为 `infra_error`），该旧单格不进入正式九格；legacy queue 与该 GLM 进程均已退出。
- Qwen smoke 已通过严格验收，1398文件 freeze 在正式启动前再次复核通过。旧监督进程 PID31148 的 `validation_failed` 是历史记录；新监督器 PID31788 已启动唯一正式 driver PID23632。
- 目前2-lane正式队列运行首个 paired block：`qwen_baseline_42` PID26468、`qwen_cam_full_42` PID33308，另7格 pending。准确进展以 `reports/RUN_STATUS.md`、`reports/v2r1/supervisor_state.json` 与 `reports/v2r1/queue_state.json` 为准。
- 子 agent 执行测试/实验，用户指定 GPT-6 Luna、high；最多三个子 agent 并行（与主 agent 合计四槽）。
- 计划 1 的六格使用独立扩展 manifest、冻结摘要、输出与状态；等待父 driver PID 23632 正常完成且原九格全部验收后再启动，最多 2 lane。父队列失败/不完整时扩展应停在 `needs_attention`。
- 扩展 manifest 必须在新增 test 结果可见前固定。三个消融分别与 CAM-Full 做 seed 内逐题配对，报告三个训练 seed 的配对差值/均值/样本 SD；每个 seed 内三项比较作为 Holm 校正族，并说明训练重复仍只有三个。
- 六格 manifest 与 runner/report/waiter 指纹已写入 `outputs/studies/cam_v2r1_qwen_ablation_repeats/manifest.json`；监督器 PID 24356 已验证父 PID 23632 和 31788 的命令、创建时间与进程关系，当前 `waiting_for_parent`，尚未发出新增模型请求。
