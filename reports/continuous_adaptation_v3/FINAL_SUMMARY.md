# CAM v3 连续适应实验最终核算

实验于2026-10-18 20:42至2026-10-19 03:34（Asia/Shanghai）顺序执行，四格均通过最终产物验收，后台正常退出。巡检 `cam-20` 已停用，没有启动扩展实验；状态凭据见 [COMPLETION.json](COMPLETION.json)，独立验收见 [FINAL_ACCEPTANCE.md](FINAL_ACCEPTANCE.md)。产物验收通过不代表每次辅助调用均成功，相关缺失见下文。

配置为 Qwen target、GLM optimizer，baseline/CAM v3 各两个训练种子。每格四步主训练按 Cell/Sheet/Cell/Sheet 交替，每步20题；同种子两组共享训练ID，使用40题验证集选出 best，再进行一次固定120题测试。预定设计见 [实验协议](../../docs/experiments/CONTINUOUS_ADAPTATION_V3.md)。

## 结果

我从四个 cell 的原始 `test_eval/results.jsonl` 独立重算了 hard pass 数和逐任务配对结果。每个 cell 都有 120 行、120 个唯一任务 ID；两组 seed 使用同一固定任务集。任务类型分布在每个 cell 均为 83 个 `cell_level`、37 个 `sheet_level`。

| seed | 方法 | Hard pass | 准确率 | 相对同 seed baseline | 配对胜 / 负 / 平 |
|---|---|---:|---:|---:|---:|
| 42 | Baseline | 68/120 | 56.67% | — | — |
| 42 | CAM v3 | 64/120 | 53.33% | -3.33 个百分点 | 13 / 17 / 90 |
| 43 | Baseline | 65/120 | 54.17% | — | — |
| 43 | CAM v3 | 70/120 | 58.33% | +4.17 个百分点 | 13 / 8 / 99 |

两个 seed 的准确率差值均值为 **+0.42 个百分点**。这是两个 seed 级配对差值的均值；不能把复用的 120 个任务当成 240 个独立任务。seed 42 CAM 下降、seed 43 CAM 上升，方向不一致；在仅两个 seed、没有 v2 对照的条件下，这组数据没有显示出明确、稳定的改进证据。

分类型看，seed 42 的 cell-level 为 46→46、sheet-level 为 22→18；seed 43 的 cell-level 为 45→48、sheet-level 为 20→22。样本量不足以支持更细的类型效果结论。

## 辅助生成与运行量

原始结果目录中核对到 **7 次 `no_content`**：5 次 slow update、2 次 meta-skill 生成。另有两种 arm 各自的首轮占位/跳过流程。slow update 实际产出内容的次数为 CAM v3 **3**、baseline **4**。这些失败按既有路径被记录为无内容并继续训练，没有回放或补写；其反事实影响无法从本次运行测得。事件清单与限制见 [INCIDENTS.md](INCIDENTS.md)。

按四个 cell `summary.json` 记录的 returned-token totals 汇总，baseline 记录输入 4,721,063、输出 7,082,338、合计 11,803,401；CAM v3 输入 3,313,459、输出 7,759,070、合计 11,072,529。覆盖标记为 `partial_legacy`，因此这些数字是**部分记录的返回 token 统计**，不是完整 HTTP 计费量，也不足以证明实际成本节省或其因果来源。

四个 cell 的记录 wall time 合计：baseline 12,175.4 秒，CAM v3 12,490.9 秒。该合计包括训练和训练后的固定测试阶段，属于本次运行观察值，不是重复实验的稳定耗时估计。

## 可复现性限制

原始 freeze manifest 没有包含 prompt Markdown 文件。运行后另行记录了当前 21 个 prompt 文件的观察及哈希，但这不能证明每次历史请求实际使用的 prompt 字节内容。该范围限制及观察目录见 [INCIDENTS.md](INCIDENTS.md) 和 `outputs/studies/continuous_adaptation_v3/prompt_observation_20261019_0125/`。因此，结果描述的是这次已完成运行，不应视为完全可复现的 prompt 冻结比较。

## 核算范围

结果来自本地四个 cell 的固定测试行、summary、辅助结果文件和已有事故记录；未发起 API 请求、未启动或重放实验，也未修改 freeze、queue、reporter、训练代码或配置。
