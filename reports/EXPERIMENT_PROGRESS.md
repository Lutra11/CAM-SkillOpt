# CAM-SkillOpt 实验进展记录

更新时间：2026-08-17

## 已完成阶段

| 阶段 | 状态 | 说明 |
| --- | --- | --- |
| 阶段 4 | 已完成 | 梳理 CAM 方法论与 SkillOpt 接入位置。 |
| 阶段 5 | 已完成 | 完成 CAM preflight：配置、模块、基础逻辑检查。 |
| 阶段 6 | 已完成 | 接入 `skillopt.cam`、trainer 运行开关和离线 sanity check。 |
| 阶段 7 | 已完成 | 确认原 `data` 中多为任务索引/manifest；真实 SpreadsheetBench 需要额外数据包。 |
| 阶段 8 | 已完成 | 建立 SpreadsheetBench 数据 materialize 脚本与环境检查。 |
| 阶段 9 | 已完成 | 建立模型配置/训练配置 preflight，梳理 DeepSeek/GLM 兼容路径。 |
| 阶段 10 | 已完成 | 完成 DeepSeek-only 最小链路配置和兼容检查。 |
| 阶段 11 | 已完成 | 建立小规模 SpreadsheetBench split 与运行入口。 |
| 阶段 12 | 部分完成 | 本地 smoke run 链路已准备；联网真实训练仍需稳定 API 访问后继续。 |

## 当前代码能力

- CAM 机制已作为独立模块放入 `skillopt/cam/`。
- Trainer 已支持 CAM 配置读取、bootstrap gate、adaptive budget 和 rejected-memory 记录。
- SpreadsheetBench 数据脚本可从真实数据包生成 SkillOpt 可运行 split。
- DeepSeek-only 配置已加入 `configs/cam_experiments/`，便于先跑最小闭环。

## 尚未完成的论文结果

- 完整 benchmark 主结果表。
- CAM full 与 baseline 的稳定多轮对比。
- 三组 ablation 结果。
- 成本、耗时、API 调用量统计。
- 最终论文初稿 Word 文档。

## GitHub 管理原则

本仓库只提交：

- 源码。
- 配置。
- 可复现实验脚本。
- 轻量数据索引。
- 人工整理后的安全报告。

不要提交：

- `.env`。
- 真实 API Key。
- 原始 Excel 数据包。
- `outputs/` 中的 prompt/result 原始日志。
- `.venv/` 和缓存目录。
