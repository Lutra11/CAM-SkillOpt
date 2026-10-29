# 连续任务适应实验：执行交接计划

> 2026-10-19 最终更新：新的 v3 四格已全部完成并通过产物验收，巡检 `cam-20` 已停用。最终摘要为 `reports/continuous_adaptation_v3/FINAL_SUMMARY.md`，独立验收为 `FINAL_ACCEPTANCE.md`，七次辅助生成缺失和提示词冻结范围限制见同目录 `INCIDENTS.md`。两个种子的 CAM 减 baseline 分别为−3.333和+4.167个百分点，均值仅+0.417个百分点；当前结果不支持明显稳定提升。以下 v1 阻塞及恢复建议全部作为历史保留，不再执行，也不自动扩展实验。

> 2026-10-18 当前入口：两角色认证检查均已返回 HTTP 200，旧401阻塞未复现。用户已授权依次执行新的 v3 四格实验，见 [CONTINUOUS_ADAPTATION_V3.md](CONTINUOUS_ADAPTATION_V3.md)。权威队列为 `outputs/studies/continuous_adaptation_v3/queue_state.json`，监督状态为 `reports/continuous_adaptation_v3/supervisor_state.json`。下文保留 v1 历史现场及当时恢复建议，不再作为当前运行步骤；不要重启旧 v1 目录。

> 后续更新：用户随后授权了门控、记忆和预算的 v3 改进及小规模验证。代码、配置和离线证据见 [CAM_V3_IMPROVEMENTS.md](CAM_V3_IMPROVEMENTS.md)。本文件记录原四格方案与401失败现场；不能把新代码直接续接旧 freeze 或假定原 runner 已使用 v3 配置。

- 更新时间：2026-10-18（Asia/Shanghai）
- 当前执行状态：阻塞于 provider 认证；正式协议 revision 2 已冻结，0/4 格通过验收。
- 适用协议：[CONTINUOUS_ADAPTATION_V1.md](CONTINUOUS_ADAPTATION_V1.md)
- 运行器：[`scripts/run_continuous_adaptation_v1.py`](../../scripts/run_continuous_adaptation_v1.py)

## 交接目标

完成精简后的 SpreadsheetBench 连续任务类型切换实验，评估 CAM-Full 在 Cell-Level 与 Sheet-Level 任务交替、重复出现时，相比 baseline 是否能更稳妥地更新技能。该设计针对 CAM 的门控、记忆和漂移保护可能发挥作用的情况；它是探索性实验，不能预设 CAM 必然胜出，也不能把结果外推为一般任务上的普遍优势。

## 冻结实验设计

共四格，必须按下列顺序执行；每格通过严格验收后才开始下一格：

| 顺序 | 方法 | 种子 | 训练 |
| --- | --- | ---: | --- |
| 1 | baseline | 42 | 4步，Cell/Sheet/Cell/Sheet，每步20题 |
| 2 | CAM-Full | 42 | 与 baseline seed42 使用相同训练题ID |
| 3 | CAM-Full | 43 | 4步，Cell/Sheet/Cell/Sheet，每步20题 |
| 4 | baseline | 43 | 与 CAM-Full seed43 使用相同训练题ID |

使用标准 `data/spreadsheetbench_split`（train/val/test = 80/40/280）。训练时每类固定一批20题，在第二次出现该类时重复同一批；同种子两方法使用相同训练ID。测试题从原 held-out test 按 instruction type 预先分层抽取120题（83 Cell-Level、37 Sheet-Level），抽样seed为20261018，按源文件顺序保存。关闭训练过程的初始技能测试；训练完成后只对 validation-authorized best skill 做一次固定120题测试。不产生单独的 final-skill test 端点。

主指标是 `test_hard`（分母120）。同时报告83/37两个任务类型桶、每个种子的 CAM-Full 减 baseline 配对差、门控/记忆/漂移记录、返回 token 和墙钟时间（可用时）。两个训练种子只能支持探索性结论；任务级置信区间不能替代训练种子不确定性。

## 当前失败与证据

首次尝试已到服务认证阶段，Qwen target 返回 HTTP 401 `Invalid token`。baseline seed42 完成0个训练步，没有固定测试结果；另外三格没有启动。该格不得评分、不得视为已完成，也不得在当前失效目录中直接续跑。

失败日志、原 queue 快照和未完成输出均已保存于：

`outputs/studies/incidents/continuous_adaptation_v1_qwen_401_20261018/`

其中 `baseline_seed42_partial/` 保存初始化配置与失败现场，`baseline_seed42.log` 保存调用错误，`incident.json` 记录了状态。原状态仍见 `reports/continuous_adaptation_v1/queue_state.json`（`needs_attention`）。此前 metadata-only split 的准备错误另存于：

`outputs/studies/incidents/continuous_adaptation_v1_id_split_schema_preflight_20261018/`

已确认问题是当前 provider 路由的认证被拒；不得读取、打印或把密钥写进报告。只允许用户在本机修复本地 `.env` 或进程环境，不要要求用户把密钥粘贴到对话。

运行器当前使用 `openai_chat` 后端指向 `hub.zgci.org`。Qwen target 的运行时凭据名优先级为 `TARGET_AZURE_OPENAI_API_KEY`、`AZURE_OPENAI_TARGET_API_KEY`、共享 `AZURE_OPENAI_API_KEY`；GLM optimizer 对应 `OPTIMIZER_AZURE_OPENAI_API_KEY`、`AZURE_OPENAI_OPTIMIZER_API_KEY` 或共享变量。只检查变量是否配置及服务端是否接受，绝不输出变量值。当前没有自动 provider/key fallback。

## 恢复步骤

1. 先由用户在本机修复当前 endpoint 的 target/optimizer 凭据；不要运行正式格来“试一下”。不要改模型、endpoint、数据或训练参数来绕过401。
2. 认证准备好后，保留原 revision 2 freeze、队列和 incident 归档。因为 baseline seed42 有部分初始化输出，正式重跑应使用独立 retry 输出/研究目录和新 freeze；不要把失败目录重新标成空白，也不要覆盖 incident。
3. 使用项目 Python：

   ```powershell
   & 'E:\MiniConda\envs\skillopt\python.exe' -X utf8 scripts/run_continuous_adaptation_v1.py --prepare
   ```

   新 retry runner 应保持 revision 2 的任务ID、测试ID、指标、训练顺序和配置不变，只改变研究/输出 namespace；冻结文件必须包括新 runner 和新输出根配置。先审查 freeze 中四格、成对训练ID、120测试ID及83/37分层。
4. 仅在本机认证已修复且新 freeze 核验通过后，按冻结队列顺序单 lane 执行：

   ```powershell
   & 'E:\MiniConda\envs\skillopt\python.exe' -X utf8 scripts/run_continuous_adaptation_v1.py --run --lanes 1
   ```

   若 retry runner 使用了新文件名/namespace，应将上述脚本路径换成新冻结的 runner。不要同时启动另一份队列或跨格并发。
5. 每格只在 strict validator 验收通过后进入下一格。遇到401、其他基础设施错误、冻结不一致、缺失结果或验收失败时立即停下，保存进程状态、日志和输出；不要跳过该格或把失败写成零分。
6. 全部四格通过后，独立检查 summary 与固定测试任务ID；按 seed 配对计算 `CAM-Full - baseline`，同时报告任务类型桶和成本。不得因结果不理想更改测试集、任务顺序、端点或排除失败任务。

## 后续追加实验

若后续要增加训练种子、组件消融、预算对照或新数据集，建立新的版本号和独立输出目录，预注册新增比较与指标。保留本方案四格作为原始主分析，不追加修改其分母、不替换120题测试子集、不把新增格追加入本版本的主结果。失败重跑属于新的运行 namespace，必须保留失败现场并明确关联到本方案。

## 交接方开始前核对

- [ ] 本机 endpoint 凭据已由用户修复；变量值从未被读取或打印。
- [ ] 原 revision 2 freeze、失败日志和 partial output 仍在 incident archive。
- [ ] retry namespace 与独立 freeze 已建立，冻结任务ID、配置和测试ID与 revision 2 一致。
- [ ] 单 lane；seed内成对；按顺序运行；每格严格验收。
- [ ] 汇报如实标注认证失败、重试和任何不完整格，不给失败格编造分数。
