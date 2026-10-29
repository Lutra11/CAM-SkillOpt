# CAM v2r1 运行状态

> 2026-10-19 最终状态：当前 v3 四格全部完成并通过验收，队列/监督器为 complete，实验进程已正常退出，巡检 `cam-20` 已停用。结果见 `reports/continuous_adaptation_v3/FINAL_SUMMARY.md` 与 `FINAL_ACCEPTANCE.md`；解释时须包含 `INCIDENTS.md` 的七次辅助生成缺失和原冻结未覆盖提示词的限制。以下是历史状态，不得作为重启任何队列的依据。

> 2026-10-18 更新：以下为历史 v2r1 运行记录。当前研究为 `continuous_adaptation_v3`，四格顺序为 baseline42 → CAM v3 42 → CAM v3 43 → baseline43。当前队列见 `outputs/studies/continuous_adaptation_v3/queue_state.json`，监督状态见 `reports/continuous_adaptation_v3/supervisor_state.json`；当前协议见 `docs/experiments/CONTINUOUS_ADAPTATION_V3.md`。不要根据下文历史 PID 或命令启动旧队列。

- 2026-10-18：用户选择审查方案 2，集中 Qwen 的成对训练重复与组件消融。
- 旧队列已停止；三个未完成 v2 CAM 单元、旧源码和状态保留在 `_archive/v2_pre_review_20261018/`。
- 正式矩阵：baseline/CAM-Full × seeds42/43/44，加 seed42 的 no-bootstrap/no-memory/no-adaptive-budget，共 9 个新单元。
- 新目录：`outputs/matrix_v2r1/`；动态队列状态：`reports/v2r1/queue_state.json`。
- 当前阶段：Qwen quick smoke 已通过；严格检查确认训练步数、动作记录、三阶段测试 ID 和分数完整一致。正式协议已冻结1398个文件（含全部1201个数据文件），启动前再次复核通过。
- 外部旧 GLM 单格验证在 baseline selection 37/40 遇到 provider 502，summary 状态为 `infra_error`；该单格不进入本次正式矩阵。Qwen smoke 的首次 final-test 调用也遇到一次 provider 502，robust runner 自动重试成功。
- 正式九格现已运行，2 lanes。当前首个 paired block：`qwen_baseline_42` PID 26468 与 `qwen_cam_full_42` PID 33308；最近读取日志时两格均已完成 selection，正在进入后续训练步；其余7格 pending。权威状态：`reports/v2r1/queue_state.json`。
- 后续计划 1、3、4 已记录于 `docs/experiments/CAM_FUTURE_PLAN_134.md`。计划 1 是三个消融各补 seeds43、44，共6格；将以独立扩展目录预登记，并等待当前九格完整验收后串行启动。
- 扩展六格配置已冻结在 `outputs/studies/cam_v2r1_qwen_ablation_repeats/manifest.json`；独立监督器 PID 24356 已验证父进程身份并处于 `waiting_for_parent`。未启动新增模型调用。
- 权威计划：`docs/experiments/CAM_V2_PLAN.md`。
- 预计生成报告：`reports/v2r1/EXPERIMENT_RESULTS.md`、`paired_results.json`、`matrix_summary.json`、`cam_analysis.json`。
- 主指标 test_hard，次指标 final_test_hard；不挑最大值。
- 运行/恢复：`E:/MiniConda/envs/skillopt/python.exe -X utf8 scripts/run_study.py --run --lanes 2`。
- 正式运行冻结后禁止修改 skillopt/scripts/tests/configs 与数据；报告和说明文件可更新。

## 后台执行链

- 首次监督进程 PID 31148 因冒烟进程被中断、缺少 summary 而正确停止，状态为历史 `validation_failed`。恢复冒烟后新监督进程 PID 31788 通过验收和 freeze 复核，启动唯一正式 driver PID 23632。
- 监督脚本：`outputs/studies/cam_v2r1_qwen/supervise_v2r1.ps1`；正式运行状态由 supervisor 与 queue_state 双重记录。
- 旧版 `run_phase_queue.py --phases b_rest,c,d,e --lanes 3` 曾被外部终端重新启动两次，均已按确切进程树停止并将 `reports/queue_state.json` 标记为 superseded。不要再使用该队列或其全局清理命令。
- 旧版 legacy queue 与外部 GLM 单格进程均已退出；没有其它模型实验进程与正式2-lane队列并发。
- 不应手工再开一条正式队列；目录锁与队列锁用于防止并发重入。
