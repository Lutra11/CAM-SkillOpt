# CAM-SkillOpt

这是一个从 `SkillOpt` 基准源码整理出的实验代码副本，用于 GitHub 版本管理和后续论文实验复现。  
原始运行目录仍保留在 `C:\CAM-SkillOpt\SkillOpt`，本仓库目录 `C:\CAM-SkillOpt\CAM-SkillOpt` 只放可公开管理的源码、配置、脚本和轻量数据索引，不包含真实 API Key、虚拟环境、模型输出、大型数据集或本地实验缓存。

## 当前包含内容

- `skillopt/cam/`：CAM 优化模块，包括 Bootstrap Gate、自适应预算和 rejected-memory 机制。
- `skillopt/engine/trainer.py`：接入 CAM 训练/评估逻辑的 SkillOpt trainer。
- `skillopt/envs/spreadsheetbench/rollout.py`：SpreadsheetBench 用例匹配兼容修正。
- `configs/cam_experiments/`：baseline、CAM full、消融实验和 DeepSeek-only smoke run 配置。
- `scripts/materialize_spreadsheetbench.py`：从真实 SpreadsheetBench 数据包生成 SkillOpt 可运行 split。
- `scripts/cam_stage*_preflight.py`：阶段化环境、模型、配置和兼容性检查脚本。
- `experiments/cam_offline_sanity.py`：离线 sanity check 实验入口。
- `reports/EXPERIMENT_PROGRESS.md`：当前阶段进展与下一步计划。
- `UPSTREAM_README.md`：原 SkillOpt README 备份。

## 不包含内容

为了安全和仓库体积，本副本刻意排除了：

- `.env` 和任何真实 API Key。
- `.venv/`、`__pycache__/` 等本地环境产物。
- `outputs/`、`ckpt/`、prompt/result 原始输出。
- `spreadsheetbench_verified_400.tar.gz`、解压后的 Excel 原始数据和 materialized split。

如果需要复现实验，请在本地重新准备数据和 `.env`。

## 快速开始

```powershell
cd C:\CAM-SkillOpt\CAM-SkillOpt
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

复制环境变量模板：

```powershell
Copy-Item .env.deepseek_glm.example .env
```

然后在 `.env` 中填入本地 API Key。`.env` 已被 `.gitignore` 排除，不要提交。

## 数据准备

将真实数据包放到本地运行目录，例如：

```text
C:\CAM-SkillOpt\SkillOpt\data\spreadsheetbench_verified_400.tar.gz
```

或复制到当前仓库的 `data/` 下，但不要提交该压缩包。随后运行：

```powershell
python scripts/materialize_spreadsheetbench.py `
  --archive data/spreadsheetbench_verified_400.tar.gz `
  --output data/spreadsheetbench_split
```

## 最小 smoke run

DeepSeek-only 最小链路配置：

```powershell
python scripts/train.py `
  --config-name cam_experiments/spreadsheetbench_cam_full_deepseek_only
```

如果只是验证配置和模块导入，优先运行：

```powershell
python scripts/cam_stage10_compat_preflight.py
python experiments/cam_offline_sanity.py
```

## 论文实验路线

本仓库对应论文中的 CAM-SkillOpt 方法：在 SkillOpt 的技能优化循环中加入三类机制：

1. Paired Bootstrap Gate：用成对 bootstrap 置信判断约束技能更新是否接受。
2. Adaptive Budget：根据候选技能表现动态调整优化预算。
3. Rejected Memory：记录被拒绝编辑，降低重复无效探索。

完整实验建议分为：

- baseline：原 SkillOpt。
- CAM full：三类机制全部开启。
- ablation：分别关闭 bootstrap gate、adaptive budget、rejected memory。
- robustness：不同任务子集、不同预算、小样本/完整集对比。

## 当前状态

代码整理副本已经与正在运行实验的 `C:\CAM-SkillOpt\SkillOpt` 解耦。  
后续建议在此目录执行 `git init`、提交首个 clean snapshot，再把持续稳定的实验代码合并进来。
