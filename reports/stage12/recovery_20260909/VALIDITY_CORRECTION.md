# Stage 12 实验有效性纠正

审计时间：2026-09-09，Asia/Shanghai。该记录替代此前将正式四组全零解释为“有效负结果”的结论。未修改、删除原实验产物，未重新运行模型请求。

## 结论

CAM-Full 的失败链路得到实际产物支持：target 认证失败 → 未生成 conversation.json → 每个 step 的 n_patches=0、action=skip_no_patches → CAM gate 没有被调用。三组消融也分别发现了明确的 401 与 invalid_refresh_token，故不能拿这些零分判断方法表现或模块贡献。

三组消融按用户要求保留跟踪状态 `validity_pending_diagnosis`，另记录独立诊断 `confirmed_auth_failure`，`paper_eligible=false`。这个待诊断标签不表示其有效性通过验收。CAM-Full 和补评标记 `invalid_auth`。

## 证据计数

| 原目录（SkillOpt/outputs 下） | results 行数 | error 字段命中认证行数 | raw trace 中 401 文件数 | invalid_refresh_token 文件数 | conversation 文件数 | patch 数 | gate used step 数 |
|---|---:|---:|---:|---:|---:|---:|---:|
| formal_cam/spreadsheetbench_terra_cam_full_seed42_w1_a1_fix1 | 48 | 48 | 52/52 | 51/52 | 0 | 0 | 0 |
| formal_cam/spreadsheetbench_terra_cam_full_seed42_w1_a1_fix2 | 24 | 20 | 26/26 | 25/26 | 0 | 0 | 0 |
| eval_only/cam_full_fix2_best_valid_seen_8_w1_a1 | 8 | 8 | 8/8 | 8/8 | 0 | — | — |
| eval_only/cam_full_fix2_best_valid_unseen_8_w1_a1 | 8 | 8 | 8/8 | 8/8 | 0 | — | — |
| formal_cam/spreadsheetbench_terra_cam_no_bootstrap_seed42_w1_a1_fix1 | 56 | 53 | 56/56 | 56/56 | 0 | 0 | 0 |
| formal_cam/spreadsheetbench_terra_cam_no_adaptive_budget_seed42_w1_a1_fix1 | 56 | 55 | 56/56 | 56/56 | 0 | 0 | 0 |
| formal_cam/spreadsheetbench_terra_cam_no_memory_seed42_w1_a1_fix1 | 56 | 53 | 56/56 | 56/56 | 0 | 0 | 0 |
| stage12_probe/spreadsheetbench_terra_cam_full_P0_w1_a1_fix1 | 24 | 0 | 0/24 | 0/24 | 24 | 2 | 1 |
| stage12_probe/spreadsheetbench_terra_cam_full_P1_w4_a1_fix2 | 24 | 24 | 24/24 | 24/24 | 0 | 0 | 0 |
| stage12_probe/spreadsheetbench_terra_cam_full_P2_w4_a2_fix2 | 24 | 24 | 24/24 | 24/24 | 0 | 0 | 0 |

计数单位不能混用：results 行对应阶段中的一次任务结果；raw trace 文件数不等于 API 请求数，单个文件可能有多次自动重试；同一样本也可能出现在不同评估阶段。error 字段可能截断，因此 raw trace 提供额外认证证据。历史结果未编码 infra_error；本次只新增分类审计，没有修改原始分数。

历史 P0 fix1 的 summary 报告 analyst calls=4，step 记录存在 2 个 patch、cam_gate_used=true。可保留为小探针链路证据。P1/P2 的低耗时与全零受认证失败污染，不能证明更快或并发不稳定。

所有受影响正式 run 的训练四步均为 skip_no_patches。没有优化器有效输出、CAM Gate 执行、预算实际变化及 Memory 实际使用的充分证据时，不能声称完成了消融验证；初始化配置存在也不能替代实际调用。

## 复核方式

运行 `audit_historical_runs.ps1` 会只读扫描列出的 10 个目录，输出不含任务正文、表格内容、凭据或完整 trace 的 JSON 汇总。已保存的 `historical_validity_audit.json` 含逐阶段 llm_ok/code_ok/exec_ok、认证错误和失败分类统计。脚本用明确的错误签名计数，不依赖 hard=0 推断认证失败。原始文件仍在实验输出目录。

`analyst_calls_reported=null` 表示 summary 缺少该类别，并不自动等价于已证明调用数为零。`summary_present` 特指 summary.json；eval-only 另通过 eval_summary_present 记录 eval_summary.json。

## 下游限制

以上 invalid / pending runs 从论文性能表、模块贡献比较和配对置信区间中排除。旧分数仅留作工程事故原始记录。需要先通过双角色认证请求、401 快速终止测试、单样本完整链路与固定四样本 P0，再恢复正式矩阵。
