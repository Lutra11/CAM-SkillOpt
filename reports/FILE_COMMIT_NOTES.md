# 文件与提交说明清单

本文件用于 GitHub 代码管理说明：当前仓库采用“路径/文件组 + 提交说明”的方式记录每类文件的用途。  
由于 SkillOpt 基准本身包含大量环境、prompt、测试与文档文件，逐文件拆成数百个 Git commit 会让历史不可读；因此这里保证每个文件都能映射到一个明确的提交说明。

## 提交历史

| Commit | 说明 | 覆盖范围 |
| --- | --- | --- |
| `18d05a1` | `chore: create clean CAM-SkillOpt snapshot` | 初始化可上传 GitHub 的干净代码快照，包含源码、配置、实验脚本、测试、文档和轻量数据索引。 |
| `dfa3924` | `chore: normalize repository attributes` | 增加 `.gitattributes`，统一文本换行和二进制文件识别。 |

## 文件/路径说明

| 路径 | 对应提交 | 简单说明 |
| --- | --- | --- |
| `.env.example` | `18d05a1` | 通用环境变量模板，不包含真实密钥。 |
| `.env.deepseek_glm.example` | `18d05a1` | DeepSeek/GLM 环境变量模板，不包含真实密钥。 |
| `.gitignore` | `18d05a1` | 排除 `.env`、虚拟环境、outputs、ckpt、原始数据包和缓存。 |
| `.gitattributes` | `dfa3924` | 固定 GitHub 仓库中文本/二进制文件处理规则。 |
| `README.md` | `18d05a1` | CAM-SkillOpt 仓库说明、快速开始、数据准备和实验路线。 |
| `UPSTREAM_README.md` | `18d05a1` | 原 SkillOpt README 备份，保留基准项目信息。 |
| `LICENSE` | `18d05a1` | 原项目许可证文件。 |
| `CHANGELOG.md` | `18d05a1` | 原项目更新记录。 |
| `CONTRIBUTING.md` | `18d05a1` | 原项目贡献说明。 |
| `SECURITY.md` | `18d05a1` | 原项目安全说明。 |
| `pyproject.toml` | `18d05a1` | Python 包配置与项目元数据。 |
| `requirements.txt` | `18d05a1` | 运行和实验依赖列表。 |
| `mkdocs.yml` | `18d05a1` | 文档站点配置。 |
| `configs/_base_/default.yaml` | `18d05a1` | 全局默认配置，已加入 CAM 默认配置段。 |
| `configs/features/` | `18d05a1` | 可组合功能配置，包含 CAM feature 开关。 |
| `configs/cam_experiments/` | `18d05a1` | CAM-SkillOpt 主实验、baseline、DeepSeek-only 和 ablation 配置。 |
| `configs/alfworld/` | `18d05a1` | ALFWorld 基准配置。 |
| `configs/docvqa/` | `18d05a1` | DocVQA 基准配置。 |
| `configs/livemathematicianbench/` | `18d05a1` | LiveMathematicianBench 基准配置。 |
| `configs/officeqa/` | `18d05a1` | OfficeQA 基准配置。 |
| `configs/searchqa/` | `18d05a1` | SearchQA 基准配置。 |
| `configs/spreadsheetbench/` | `18d05a1` | SpreadsheetBench 基准配置。 |
| `data/README.md` | `18d05a1` | 数据目录说明。 |
| `data/*_id_split/` | `18d05a1` | 轻量任务 ID split manifest，不含原始任务数据。 |
| `data/*_path_split/` | `18d05a1` | 轻量任务 path split manifest，不含大数据包。 |
| `docs/` | `18d05a1` | 原 SkillOpt 文档、安装说明、配置说明和开发指南。 |
| `experiments/cam_offline_sanity.py` | `18d05a1` | CAM 离线 sanity check，用于无联网验证核心逻辑。 |
| `reports/EXPERIMENT_PROGRESS.md` | `18d05a1` | 当前阶段进展、已完成工作和未完成论文结果。 |
| `reports/FILE_COMMIT_NOTES.md` | 当前提交 | 本文件，记录文件/路径与提交说明映射。 |
| `scripts/train.py` | `18d05a1` | 训练入口，支持加载项目 `.env` 和 DeepSeek-only 最小链路。 |
| `scripts/eval_only.py` | `18d05a1` | 评估入口脚本。 |
| `scripts/materialize_searchqa.py` | `18d05a1` | SearchQA 数据 materialize 脚本。 |
| `scripts/materialize_spreadsheetbench.py` | `18d05a1` | SpreadsheetBench 真实数据包 materialize 脚本。 |
| `scripts/cam_stage5_preflight.py` | `18d05a1` | 阶段 5 配置/模块 preflight。 |
| `scripts/cam_stage8_env_preflight.py` | `18d05a1` | 阶段 8 SpreadsheetBench 环境 preflight。 |
| `scripts/cam_stage9_model_preflight.py` | `18d05a1` | 阶段 9 模型配置 preflight。 |
| `scripts/cam_stage10_compat_preflight.py` | `18d05a1` | 阶段 10 DeepSeek/GLM 兼容性 preflight。 |
| `scripts/run_*.sh` | `18d05a1` | 原项目 shell 运行示例。 |
| `skillopt/cam/` | `18d05a1` | CAM 核心方法模块：paired bootstrap gate、自适应预算、rejected memory。 |
| `skillopt/config.py` | `18d05a1` | 配置加载器，支持 CAM 配置段和 UTF-8 YAML 读取。 |
| `skillopt/engine/` | `18d05a1` | 训练循环与 CAM 接入逻辑。 |
| `skillopt/envs/spreadsheetbench/` | `18d05a1` | SpreadsheetBench 环境、rollout、执行器与 prompt。 |
| `skillopt/envs/alfworld/` | `18d05a1` | ALFWorld 环境适配。 |
| `skillopt/envs/docvqa/` | `18d05a1` | DocVQA 环境适配。 |
| `skillopt/envs/livemathematicianbench/` | `18d05a1` | LiveMathematicianBench 环境适配。 |
| `skillopt/envs/officeqa/` | `18d05a1` | OfficeQA 环境适配。 |
| `skillopt/envs/searchqa/` | `18d05a1` | SearchQA 环境适配。 |
| `skillopt/model/` | `18d05a1` | LLM 后端封装与路由。 |
| `skillopt/optimizer/` | `18d05a1` | SkillOpt 优化器、技能改写、选择和调度逻辑。 |
| `skillopt/gradient/` | `18d05a1` | 反思/梯度聚合相关逻辑。 |
| `skillopt/evaluation/` | `18d05a1` | 原 SkillOpt 评估 gate 逻辑。 |
| `skillopt/prompts/` | `18d05a1` | 通用优化/分析/合并 prompt 模板。 |
| `skillopt/utils/` | `18d05a1` | JSON、scoring 等工具函数，包含 item-level scoring。 |
| `skillopt/types.py` | `18d05a1` | 项目共享类型定义。 |
| `tests/` | `18d05a1` | 原项目与新增兼容性测试。 |

## 上传前安全结论

- 未提交 `.env`。
- 未提交真实 API Key。
- 未提交 `.venv/`。
- 未提交 `outputs/` 或 `ckpt/`。
- 未提交 `spreadsheetbench_verified_400.tar.gz`、解压 Excel 数据或 materialized split。
