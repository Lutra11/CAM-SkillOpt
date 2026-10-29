# CAM-SkillOpt 阶段 A 基线静态审计（BASELINE_AUDIT）

审计时间：2026-10-17（基于审计时刻的文件状态；qwen 单元仍在运行中，行数持续增长）
审计范围：仅只读分析。数据源：

- `outputs/matrix/spreadsheetbench/glm/static_seed42/results.jsonl`
- `outputs/matrix/livemathematicianbench/glm/static_seed42/results.jsonl`
- `outputs/matrix/officeqa/glm/static_seed42/results.jsonl`
- `outputs/matrix/spreadsheetbench/qwen/static_seed42/results.jsonl`（未完成，仅对照）

审计前已逐文件抽取首行并打印全部键确认行结构（不假设字段存在）：

- **spreadsheetbench**（glm/qwen 同构）：`id, ok, instruction_type, task_type, task_description, phase, fail_reason, llm_ok, code_ok, exec_ok, n_cases, n_exec_pass, n_pass, soft, hard, n_turns, cases, error, spreadsheet_preview, target_system_prompt, target_user_prompt, status, failure_type, wall_time_s, conversation_path, raw_trace_path`（280/280 行全字段齐备）
- **livemathematicianbench**：`id, question, task_type, hard, soft, predicted_answer, predicted_label, predicted_text, correct_label, correct_text, response, fail_reason, agent_ok, n_turns`（124/124 行齐备；**无 per-row 时间字段**）
- **officeqa**：`id, question, task_type, task_description, predicted_answer, response, ground_truth, source_files, resolved_source_paths, oracle_parsed_pages_included, oracle_parsed_pages_chars, use_local_tools, hard, soft, fail_reason, agent_ok, n_turns, last_finish_reason, target_system_prompt, target_user_prompt`（172/172 行齐备；**无 per-row 时间字段**）

---

## 1. 总表

| benchmark | n | 期望 n | hard 正确率 | soft 均值 | 失败类型分布 | 单题耗时 median/mean/p90 | wall-clock 跨度 | 闸门判定（判据区间） |
|---|---|---|---|---|---|---|---|---|
| SpreadsheetBench glm | 280 | 280 | **0.4036** (113/280) | 0.4036 | score_mismatch 135 / none(通过) 113 / code_missing 17 / execution_error 12 / code_syntax_error 2 / task_timeout 1 | 20.2 s / 50.3 s / 152.4 s（wall_time_s 字段） | 31.3 min（文件 mtime 01:12–01:43，2026-10-17） | **通过**（0.05–0.90） |
| LiveMathematicianBench glm | 124 | 124 | **0.3145** (39/124) | 0.3145 | MCQ 答错 84 / 答对 39 / task-timeout-660s 1 | 无 per-row 时间字段；n_turns median 1 / mean 1.40 / p90 2 | 48.7 min（文件 mtime 20:41–21:30，2026-10-16） | **通过**（0.30–0.95；0.3145 贴近下界 0.30，属边缘通过） |
| OfficeQA glm | 172 | 172 | **0.3081** (53/172) | 0.3428 | 答案不匹配 100 / 答对 53 / 模型未产出工具请求或最终答案 14 / agent 拼错语料路径致 FileNotFoundError 3 / 超出工具轮次预算(12) 2 | 无 per-row 时间字段；n_turns median 13 / mean 16.19 / p90 30 | 44.6 min（文件 mtime 01:46–02:12，2026-10-17） | **通过**（0.10–0.80） |
| SpreadsheetBench qwen（未完成） | 81* | 280 | 0.3580（审计时刻，随进度波动） | 0.3580 | score_mismatch 42 / none 26 / execution_error 4（首 72 行统计） | 62.2 s / 62.8 s / 87.6 s（首 72 行 wall_time_s） | 46.8 min（截至审计时刻，仍在运行） | 不适用（对照单元，不参与闸门） |

\* qwen 行数在审计期间持续增长（任务开始时约 71 → 审计过程中 72 → 76 → 81），说明该评测正在进行中。

三个 glm 基线全部落在 docs/experiments/CAM_SKILLOPT_EXECUTION_PLAN.md 阶段 A 的判据区间内，**闸门通过，可进入阶段 B**。

## 2. 完整性检查

| 文件 | 行数 | id 重复 | split 对照结果 |
|---|---|---|---|
| spreadsheetbench glm | 280 | 无（280 个唯一 id） | 与 `data/spreadsheetbench_split/test/items.json`（280 条，test/train/val = 280/80/40）的 id 集合**完全一致**：缺失 0、多余 0 |
| livemathematicianbench glm | 124 | 无（124 个唯一 id） | 与实际使用的生成式 split `outputs/matrix/livemathematicianbench/glm/static_seed42/_generated_splits/livemathematicianbench_2-1-7_seed42/test/items.json`（124 条，test/train/val = 124/35/18，合计 177）**完全一致**：缺失 0、多余 0 |
| officeqa glm | 172 | 无（172 个唯一 id） | 与 `data/officeqa_split/test/items.json`（172 条；split_manifest.json 声明 2:1:7 seed42 → train/val/test = 49/25/172）**完全一致**：缺失 0、多余 0 |
| spreadsheetbench qwen | 81（运行中） | 无 | 已跑的 81 个 id 均在 test split（280 条）内，多余 0；尚缺 199 条未完成 |

关于 livemathematicianbench 的 split 来源说明：`data/livemathematicianbench_split/` 下只有 4 个月度原始题库文件 `qa_202511/202512/202601/202602_final.json`（29+52+46+50 = 177 题，无 id 字段、只有 `no`+`month`），**不含** train/test 划分清单；实际 2:1:7 划分由运行时生成在单元目录的 `_generated_splits/` 下（id 格式 `月份-序号`，如 `202602-12`）。审计以生成式 split 为准，177 = 124+35+18 自洽。

## 3. 有效性检查

- **hard 为 null 或缺失**：四个文件均无（spreadsheetbench 280/280、livemath 124/124、officeqa 172/172、qwen 81/81，hard 全部为数值 0/1）。
- **status 为 infra_error/error 的行**：spreadsheetbench glm/qwen 的 280+81 行 status 全部为 `completed`，无 infra_error/error。
- **spreadsheetbench llm_ok=False 的行**：glm 0 行，qwen 0 行 —— 不存在"模型没被调用"的伪结果。（注：执行计划风险表第 1 条提到的历史 bug——lmb 基线因 id 含冒号曾全 0——在当前数据中已不复现，当前 id 均为 `202601-20` 这类连字符格式，hard 率 0.3145 正常。）
- **n_cases 与 n_pass 自洽性（spreadsheetbench）**：两文件均无 `n_cases==0` 行、无 `n_pass>n_cases` 行；"hard==1 ⇔ n_pass==n_cases 且 hard==0 ⇔ n_pass==0" 的等价关系在 280+81 行上零违例（soft 亦与 hard 一致取 0/1）。
- **其他失败明细（供参考，均按 hard=0 正常计分）**：
  - livemath 124 行中 1 行（`202512-38`）`agent_ok=False`、fail_reason=`task-timeout-660s`，为 660 秒任务超时，按失败计分，非基础设施错误；
  - officeqa 172 行中 19 行 `agent_ok=False`：14 行"Model neither produced a tool request nor a final answer"、2 行超出 12 轮工具预算、3 行（UID0171/0188/0214）因 **agent 自行拼错语料路径**触发 FileNotFoundError——经核实 `resolved_source_paths` 中正确路径的语料文件均实际存在（agent 把路径段重复/错拼，如 `...transformed\treasury_bulletins_parsed\transformed\...`），属 agent 行为失败而非语料缺失或评测器缺陷。

## 4. 密钥泄漏检查

对三个 glm 单元目录（外加 qwen 对照单元）下所有 `*.json / *.jsonl / *.txt / *.md` 文本文件（共 **4149** 个：spreadsheetbench glm 2523、livemath glm 378、officeqa glm 518、spreadsheetbench qwen 730）做正则扫描（形如 `sk-` 开头且后续长度 ≥ 32 的字母数字串）：

- **命中文件数：0**。四个单元目录均未发现任何疑似 API 密钥的字符串，无需脱敏处理。

## 5. 结论

1. **三个 glm 基线全部通过阶段 A 闸门**（0.4036 ∈ [0.05,0.90]；0.3145 ∈ [0.30,0.95]；0.3081 ∈ [0.10,0.80]），行数、id 唯一性、split 对应关系、hard 有效性、n_cases/n_pass 自洽性、密钥安全全部干净，**可直接用作后续训练对比的基线**。
2. LiveMathematicianBench 的 0.3145 仅高出下界 0.0145，属**边缘通过**：多选题 4 选 1 的随机基线约 0.25，说明模型只比随机略好；若后续认为该 benchmark 优化空间需重新评估，应在阶段 B 前讨论，但按现行判据无需停止。
3. OfficeQA 的 3 行 agent 拼错语料路径失败值得在阶段 B 复现观察（正确路径的文件存在），但目前仅占 1.7%，不影响基线可用性。
4. **qwen 部分基线**：审计时刻 81/280 行，hard 率 0.3580（glm 同口径 0.4036）；单行 wall_time_s median 62.2 s / mean 62.8 s（首 72 行），实测整体吞吐约 37 s/行（含并行）。**预计剩余约 199 题**：按实测吞吐折算约 **2.0–2.5 小时** wall-clock（若纯串行则约 3.5 小时）可完成。建议等其跑完后再做 glm vs qwen 对照。
