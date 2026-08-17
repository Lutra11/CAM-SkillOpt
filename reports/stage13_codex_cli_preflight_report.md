# Stage 13 - Codex CLI 实验前置检查报告

时间：2026-08-17

## 本阶段目标

按照 `PLAN.md` 继续推进实验，在启动 SpreadsheetBench Codex CLI smoke run 前，先完成必要的代码接入与前置验证。

## 已完成

1. 注册 `optimizer_backend=codex_cli`
   - 修改 `skillopt/model/common.py`
   - 修改 `skillopt/model/backend_config.py`
   - 修改 `skillopt/model/__init__.py`

2. CLI 参数兼容
   - `scripts/train.py` 已接受 `codex_cli`
   - `scripts/eval_only.py` 已接受 `codex_cli`

3. 复用已有 Codex optimizer 实现
   - optimizer 走 `skillopt/model/codex_backend.py`
   - 调用方式为 `codex exec --json`
   - optimizer sandbox 默认保持 `read-only`

4. target 继续使用现有 `codex_exec`
   - target 走 `skillopt/model/codex_harness.py`
   - smoke run 配置中 target sandbox 为 `workspace-write`
   - network/web_search 默认关闭

5. 配置解析 dry-run 通过

关键解析结果：

```json
{
  "env": "spreadsheetbench",
  "optimizer_backend": "codex_cli",
  "target_backend": "codex_exec",
  "optimizer_model": "gpt-5.6-sol",
  "target_model": "gpt-5.6-terra",
  "num_epochs": 2,
  "train_size": 8,
  "batch_size": 4,
  "seed": 42,
  "minibatch_size": 2,
  "merge_batch_size": 2,
  "analyst_workers": 1,
  "max_analyst_rounds": 1,
  "slow_update_samples": 2,
  "sel_env_num": 4,
  "test_env_num": 4,
  "eval_test": true,
  "mode": "single",
  "limit": 8,
  "workers": 1,
  "exec_timeout": 300,
  "codex_exec_use_sdk": "cli",
  "codex_exec_sandbox": "workspace-write",
  "codex_exec_full_auto": false,
  "codex_exec_network_access": false,
  "codex_exec_web_search": false,
  "out_root": "C:\\CAM-SkillOpt\\SkillOpt\\outputs\\smoke\\codex_spreadsheet_terra"
}
```

## 当前阻塞

执行：

```powershell
codex --version
```

结果：

```text
Access is denied
```

定位结果：

```text
C:\Program Files\WindowsApps\OpenAI.Codex_26.803.10989.0_x64__2p2nqsd0c76g0\app\resources\codex
C:\Program Files\WindowsApps\OpenAI.Codex_26.803.10989.0_x64__2p2nqsd0c76g0\app\resources\codex.exe
```

原因判断：

- 当前 `codex` 指向 WindowsApps 受保护目录；
- 即使申请外部执行权限，实验进程仍无法运行该 `codex.exe`；
- 这不是 CAM-SkillOpt 代码错误，也不是模型 API 错误，而是 Codex CLI 可执行文件权限/安装路径问题。

## 下一步建议

在继续 smoke run 前，需要先解决 Codex CLI 可执行路径：

1. 在普通 PowerShell 中确认你本人账号能否运行：

```powershell
codex --version
```

2. 如果普通 PowerShell 也拒绝访问，建议安装一个非 WindowsApps 路径的 Codex CLI，并设置：

```powershell
$env:CODEX_EXEC_PATH="实际可执行的 codex 路径"
$env:CODEX_CLI_BIN="实际可执行的 codex 路径"
```

3. 如果普通 PowerShell 可以运行，但 Codex 任务里不能运行，需要把可执行文件复制/安装到用户可访问路径，再配置 `CODEX_EXEC_PATH` 与 `CODEX_CLI_BIN`。

## 本阶段结论

Stage 13 代码接入和配置 dry-run 已完成；正式 smoke run 尚未启动。  
当前必须先解决 `codex.exe` 权限问题，否则计划中的 `codex_cli + codex_exec` 实验链路无法执行。
