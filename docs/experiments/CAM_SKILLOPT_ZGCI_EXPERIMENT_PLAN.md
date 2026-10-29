# CAM-SkillOpt 新实验方案（ZGCI Model Hub 版）

- 制定时间：2026-10-16
- 状态：**待用户批准后执行**
- 取代：`docs/experiments/CAM_CODEX_CLI_EXPERIMENT_PLAN.md`（Codex CLI 版，已废弃）
- 配套档案：`docs/experiments/ZGCI_MODEL_HUB_API.md`（接口与模型调研）

---

## 0. 结论速览

| 问题 | 结论 |
| --- | --- |
| 有哪些模型可用 | 17 个，可做文本对话的只有 **4 个真实模型**：GLM-5.3、Qwen3.6-35B-A3B、Gemma4-26B，外加 2 个身份不可信的聚合路由 |
| 费用 | **全部免费**（`/api/pricing` 中 `model_ratio = 0`、`model_price = 0`） |
| 要改代码吗 | **不用改代码**。仓库已内置 `openai_compatible` 认证模式，纯配置即可接上 |
| 现有实验设计要改吗 | **要改**：改**后端**、改**配置陷阱**、改**规模**，**算法不动** |
| 旧实验为什么没结果 | **不是模型不行，也不是规模太小 —— 是鉴权失败**。旧跑在 target 出码之前就被 HTTP 401 污染，token 统计 56 次调用全部 0 token |
| 旧结果能用吗 | **一个都不能用**。目前**不存在任何一个完整有效的正式跑** |
| DSH 要加插件吗 | **不需要加任何插件**。建议只加一个项目内 skill 记录实验协议（见第 7 节） |

---

## 1. 接口与可用模型（摘要）

完整调研见 `docs/experiments/ZGCI_MODEL_HUB_API.md`。

### 1.1 接口

```text
Base URL     https://hub.zgci.org/v1          ← 必须带 /v1
Chat 端点    POST /v1/chat/completions
模型列表     GET  /v1/models
定价 JSON    GET  /api/pricing                ← 页面是 JS 渲染，必须读 JSON
鉴权         Authorization: Bearer <API_KEY>
协议         OpenAI Chat Completions 兼容（New API 网关）
当前分组     oc (OpenClaw)，group_ratio = 1
```

**关键发现：全部 17 个模型 `model_ratio = 0`、`model_price = 0`，即该 key 调用所有模型免费。本实验没有成本约束，唯一约束是时间与并发。**

### 1.2 别名 → 真实模型（已实测）

| 别名 | 真实后端 | 定位 | 延迟 |
| --- | --- | --- | --- |
| `glm` | `glm-5.3-private-19428658` | **强** | 1–3 s |
| `glm-anthropic` | `glm-5.3-private-19428658` | 强（Anthropic 协议入口） | 1–17 s 抖动 |
| `qwen` | `Qwen3.6-35B-A3B` | **弱**（35B 总参 / 3B 激活 MoE） | 0.5–5 s |
| `minimax` | `Qwen3.6-35B-A3B` | 弱（与 `qwen` 同后端） | 0.5–5 s |
| `gemma4:26b` | `gemma4:26b` | 中 | 6–12 s |
| `smart-router` | 自报不一致 | 身份不可信 | 0.5–3 s |
| `smart-router-v2` | 自报 GLM-5.3 | 身份不可信 | 1–2 s |

其余 10 个是向量/重排/语音/图像模型，本实验不用。

**只有别名能调用**：`glm-5.3`、`glm-4.6`、`Qwen3.6-35B-A3B`、`MiniMax-M2.7`、`deepseek-chat`、`kimi-k2` 等具体版本名全部返回 `model_not_found`。

### 1.3 能力矩阵（来源：平台前端硬编码的权威模型文档）

| 模型 | 参数量 | 上下文 | 输出上限 | 图片 | 工具/JSON | 严格 JSON 实测 |
| --- | --- | --- | --- | --- | --- | --- |
| `glm` | 753B MoE（256 专家，激活 8+1） | **1,000,000** | 131,072 | ❌ | ✅ / ✅ | ✅ |
| `qwen` | 35B 总参 / 3B 激活 | 262,144 | **与上下文共用** | ✅ | ✅ / ✅ | ❌ |
| `gemma4:26b` | 26B（激活约 4B） | — | — | ✅ | ✅ / ✅ | ❌ |
| `smart-router` | GLM 约 753B / Qwen 35B | 262,144（取小） | 131,072 | ✅ | ✅ / ✅ | 身份不可信 |
| `smart-router-v2` | GLM-5.3 作答 + Qwen 看图 | 1,000,000 | 131,072 | ✅ | ✅ / ✅ | 身份不可信 |

参数兼容性 **22/22 全部通过**（`max_completion_tokens` / `reasoning_effort` / `response_format` 等均被接受），**无需为参数兼容性改代码**。

### 1.3.1 关于"关闭思考"的实测结论（`scripts/probe_zgci_thinking.py`）

`reasoning_effort` 在各模型上的行为**完全不同**，不能当作统一旋钮：

| 模型 | 关思考的正确做法 | 实测证据 |
| --- | --- | --- |
| `qwen` | **`reasoning_effort: "none"`** | 965 tok → **4 tok**；reasoning 2346 字符 → 0。四种写法（`none` / `enable_thinking=false` / `thinking={type:disabled}` / `chat_template_kwargs`）效果一致，SkillOpt 发的键就在其中 |
| `glm` | **不要传**（保持 `""`） | 传 `none` → 输出污染成 `725725`；传 `medium` → **烧满 8192 tok、89 秒、被截断**。默认参数输出干净 |
| `gemma4:26b` | **关不掉** | 10 个变体里 9 个输出**完全相同**（728 tok / 1524 字符）；`reasoning_effort` 对它无任何效果 |

**→ 因此 "glm 开思考 vs glm 关思考" 这个对照不成立，已删除。** 「开关思考」的对照改由 `qwen` 承担。

> 附带发现：`qwen` 关掉思考后答案从 725（正确）变成 650（错误），说明思考对该模型确有实际作用 —— 这让"开关思考"成为一个有意义的实验轴。

### 1.4 必须避开的陷阱

1. **空响应**：`qwen` 的**输入与输出共用同一个上下文窗口**（平台文档原文），所以 reasoning 会直接挤占正文预算。实测 `max_tokens=256` 时 reasoning 烧掉 932 字符、`content` 为**空字符串**。**→ target 为 `qwen` 时必须 `max_completion_tokens ≥ 2048`（建议 16384）。**
2. **`glm` 的 `<think>` 残留是探针假象**：用"只回一个整数"这类提示词时，`glm` 会把思考残片倒进 `content`（5 次里 3 次污染）。但**真实任务的 20 条预测中 0/20 出现该现象**（系统提示词要求输出 ```python 代码块，模型先给代码块，`extract_code` 取第一个代码块，不受影响）。**主实验不受影响。**
3. **网络**：`huggingface.co` 在本机**被阻断**，必须走 `hf-mirror.com`。且 HF 仓库的递归 tree 接口有 **1000 条上限**，会静默截断大仓库，需逐目录列举。

---

## 2. 接入方式：不需要改代码

仓库已内置 OpenAI 兼容模式：

```python
# skillopt/model/azure_openai.py:294-300
if auth_mode in {"openai_compatible", "compat", "openai"}:
    return OpenAI(base_url=cfg["endpoint"].rstrip("/"), api_key=cfg["api_key"] or "dummy", ...)
```

现成先例：`configs/cam_experiments/spreadsheetbench_cam_full_deepseek_only.yaml` 就是用这套键接的 DeepSeek。我们照抄结构换成本网关。

### 2.1 配置取值

| 项 | 值 |
| --- | --- |
| `model.optimizer_backend` / `target_backend` | `openai_chat` |
| `model.optimizer_azure_openai_auth_mode` / `target_...` | `openai_compatible` |
| `model.optimizer_azure_openai_endpoint` / `target_...` | `https://hub.zgci.org/v1` |
| `model.optimizer` | `glm` |
| `model.target` | `glm`（强）/ `qwen`（弱） |
| `model.reasoning_effort` | `""`（置空，减少变量） |
| `env.mode` | `single`（SpreadsheetBench 必须） |

密钥只放 `.env`（已 gitignore）：

```dotenv
OPTIMIZER_AZURE_OPENAI_API_KEY=sk-...
TARGET_AZURE_OPENAI_API_KEY=sk-...
```

### 2.2 三个坑

1. **endpoint 不写 `/v1` 会 404** —— OpenAI SDK 只在 `base_url` 后拼 `/chat/completions`。
2. **不显式写 `openai_compatible` 会去调 Azure CLI** —— 默认 `AUTH_MODE=azure_cli`（`azure_openai.py:33`），会执行 `az account get-access-token` 必然失败，且报错**看起来像凭证问题而不是配置问题**（这正是旧实验踩坑的表象）。
3. **模型名不能落进 Responses API 名单** `{gpt-5.x-codex, codex-mini, gpt-5.4-pro}`，否则会走 `POST /responses`。`glm` / `qwen` 不在名单内，安全。

---

## 3. 旧实验为什么没结果（已核对原始文件）

### 3.1 直接死因：鉴权失败，不是模型不行

`reports/stage12/formal_cam_no_bootstrap_seed42_w1_a1_fix1/summary.json` 实测：

```json
"token_summary": { "rollout": { "calls": 56, "prompt_tokens": 0,
                                "completion_tokens": 0, "total_tokens": 0 } }
```

**56 次调用、0 token。** 请求根本没成功返回。结合 `recovery_20260909/VALIDITY_CORRECTION.md`，失败模式是 **HTTP 401 / `invalid_refresh_token`**，且发生在 **target 出码之前**。

配套证据：

```json
{ "baseline_selection_hard": 0.0, "best_selection_hard": 0.0, "test_hard": 0.0,
  "total_steps": 4, "total_accepts": 0, "total_rejects": 0, "total_skips": 4,
  "current_origin": "slow_update_placeholder_epoch_01", "best_origin": "initial_skill" }
```

`0.0` 分与 `4 个 step 全 skip` 是 401 的**后果**，不是 CAM 的结论。

### 3.2 一个反证：链路本身是通的

同一批文件里，原版 SkillOpt 基线跑（`formal_normal_train16_sel8_t420_seed42`）达到了 `best_selection_hard = 0.5`，**1 accept / 3 rejects**。说明训练循环在鉴权正常时能工作 —— 只是那次 test 阶段被中断，指标为 null。

**所以：管线是通的，但至今没有任何一个正式跑完整跑完并有效。**

### 3.3 次要死因：即使鉴权正常，当时的 CAM 配置也跑不出有效结果

| # | 问题 | 后果 |
| --- | --- | --- |
| A | `configs/cam_experiments/spreadsheetbench_cam_full.yaml` 继承 `evaluation.sel_env_num: 0`；而 `trainer.py:1282` 要求 `expected_n == int(cfg["sel_env_num"])` | 抛 `SelectionEvidenceError` → `summary.json {status: invalid_selection_evidence, eligible_for_paper: false}`。**该配置原样跑不了** |
| B | `configs/spreadsheetbench/default.yaml:30` 是 `mode: multi` | exec target 后端会 `NotImplementedError`（`adapter.py:76-79`）。用 `openai_chat` 无此限制，但仍应显式置 `single` |
| C | `cam.max_budget: 8` vs `optimizer.learning_rate: 4` | Adaptive Budget 机制性饱和到 8（失败只有 1 类 → 置信度 1.0），相当于把预算翻倍。**这是混淆变量**，让 `no_adaptive_budget` 不是干净的"只关机制"对照 |
| D | adaptive 开启时 `scheduler.step()` 被绕过（`trainer.py:1741`） | `lr_scheduler: cosine` 失效 |
| E | `cam.bootstrap_gate=false` 恢复的标量门控（`trainer.py:1929-1942`）与 SkillOpt 基线**是同一代码路径** | `no_bootstrap` = 基线门控 + adaptive + memory，命名要如实 |
| F | `--cfg-options` 用平坦键（如 `cam_enabled=false`）会被 `flatten_config` **静默丢弃** | 会**悄悄跑错实验臂**。必须用点号形式 `cam.enabled=false` |
| G | `_cam_signature`（`trainer.py:426-445`）把 `trainer.py`、`cam/selection_guard.py`、`cam/bootstrap_gate.py` 的 sha 绑进证据签名 | **改这三个文件会让所有历史跑失去可比性 → 本实验不改代码** |

### 3.3.1 本次新发现并已修复的代码缺陷

| # | 缺陷 | 证据 | 修复 |
| --- | --- | --- | --- |
| H | **`scripts/eval_only.py` 从不加载 `.env`** —— 只有 `train.py` 会加载。空 key 被 `_make_client` 当作字面量 `"dummy"` 发出，返回 401 | 实测：同一配置下 `train.py` 可用，`eval_only.py` 报 `AuthenticationError 401 Invalid token`。**这解释了历史上 `eval_only` 全部得 0 分** | 已在 `eval_only.py` 加入与 `train.py` 一致的 `.env` 加载器（可被 `SKILLOPT_LOAD_DOTENV=0` 关闭） |
| I | **`livemathematicianbench/dataloader.py:20` 的 `open()` 未指定 `encoding`** —— Windows 中文环境默认 GBK，读含非 GBK 字符的 JSON 直接抛 `UnicodeDecodeError` | 实测：`load_items()` 在 position 62079 处崩溃；改用 UTF-8 模式后正常载入 177 条 | 不改该文件（`_cam_signature` 不含它，但保持上游代码不变更安全）。改为**全流程强制 `python -X utf8` 启动**，由 `scripts/run_experiment.py` 自动 re-exec 保证 |

> 修复 H 之后，单题真实评测首次拿到 `status=completed`（`failure=score_mismatch`，代码执行与单元格比对跑通），这是历史上从未达到过的状态。

### 3.3.2 环境级阻塞：系统代理会静默丢弃校内网关的 TLS 握手（最关键的一条）

**症状**：`hub.zgci.org` 所有 HTTP 请求 30–40 s 超时，但——

| 观测 | 结果 | 说明 |
| --- | --- | --- |
| DNS | ✅ `hub.zgci.org` → `10.1.132.25` | 校内地址 |
| TCP 443/80/8080 | ✅ 0.0–0.2 s 连上 | 不是防火墙丢包 |
| **裸 socket + TLS + 手工 HTTP** | ✅ **`HTTP/1.1 200 OK`，0.1 s** | **网关本身完全健康** |
| 裸 socket 连续 6 次 POST | ✅ **6/6 成功** | 服务稳定 |
| `http.client` | ✅ 成功 | 不用系统代理 |
| `urllib` / `requests` / `httpx` / **`openai` SDK** | ❌ **全部超时** | **都自动读取系统代理** |
| `httpx(trust_env=False)` | ✅ 成功 | 确证是代理问题 |

**根因**：本机有一个**系统级代理 `127.0.0.1:7890`**（Clash）。它写在 **Windows 注册表**里，所以 `Get-ChildItem env:` 看不到、`HTTP_PROXY`/`HTTPS_PROXY` 环境变量也是空的，但 Python 的 `urllib.request.getproxies()` 会从注册表读到它，于是 `urllib`/`requests`/`httpx`/`openai` **全部自动走这个代理**。

该代理能通外网（`pypi.org`、`hf-mirror.com` 均正常 200），但**会把校内网关 `hub.zgci.org` 的 TLS 握手丢弃**，表现为 `_ssl.c:1015: The handshake operation timed out`。

**修复**：让校内主机绕过代理，同时保留外网走代理（否则 `hf-mirror` 下载会断）。写入 `.env`：

```dotenv
NO_PROXY=hub.zgci.org,gateway.zgci.org,10.1.132.25,localhost,127.0.0.1
no_proxy=hub.zgci.org,gateway.zgci.org,10.1.132.25,localhost,127.0.0.1
```

验证：加上之后 `openai` SDK 与 `urllib` 均在 **0.0 s** 内返回 `200 / PING`。

> ⚠️ **这条对旧实验的解读有影响**：旧实验用 `openai`/`httpx` 系客户端，**同样会经过这个代理**。旧日志里的 `APIConnectionError`、`network_error`、以及部分"超时"类失败，很可能有一部分是**代理造成的，而非模型或鉴权问题**。旧报告把这些一律归因为鉴权污染，可能并不完全准确。
> （旧日志里确实出现过 HTTP 401 —— 那是真的到达了服务端并被拒；但"超时"类失败与本次代理症状一致。）

**排查这类问题的通用方法**（记下来，换机器时先跑）：

```python
python -X utf8 -c "import urllib.request; print(urllib.request.getproxies())"
```
若输出里出现 `127.0.0.1:xxxx`，而目标又是内网地址，就必须配 `NO_PROXY`。

### 3.4 结论

旧结果**整体作废**。新实验与旧实验的关系是：**第一次把一个从没跑通的链路真正跑通，并第一次拿到有效数据。** 所以"最小连通性验证"不是走过场，而是本次实验最关键的一步。

---

## 4. 新实验设计

### 4.1 设计变更清单

| 维度 | 旧设计 | 新设计 | 理由 |
| --- | --- | --- | --- |
| optimizer 后端 | `codex_cli` | **`openai_chat`** | 去掉 CLI / ChatGPT 登录态依赖，这是 401 的根源；且 `codex_cli` 绕过请求级用量账本导致 token 统计天生不完整 |
| target 后端 | `codex_exec` | **`openai_chat`** | 同上。SpreadsheetBench 本就是纯文本 codegen，不需要 agent 沙箱 |
| optimizer 模型 | `gpt-5.6-sol` | **`glm`** | 本网关唯一严格 JSON 过关的模型 |
| target 模型 | `gpt-5.6-terra/luna/sol` | **`glm`（强）/ `qwen`（弱）** | 免费额度下用"强弱差"构造噪声信号 |
| `sel_env_num` | 继承 0（**必崩**） | **显式设 20** | 修 3.3-A |
| `cam.max_budget` | 8 | **4（= `optimizer.learning_rate`）** | 修 3.3-C 混淆变量 |
| `env.mode` | 继承 `multi` | **显式 `single`** | 修 3.3-B |
| 规模 | train 16 / 1 epoch | **train 80 / 4 epoch** | 让 bootstrap 有成对样本、跨 epoch 才能测 Rejected Memory |
| `--cfg-options` | 平坦键 | **点号键** | 修 3.3-F |
| 成本约束 | 有 | **无** | 约束只剩时间 |

**算法与 CAM 实现完全不动**（`skillopt/cam/**`、`trainer.py` 一行不改）—— 既因为当前没有 bug 级缺陷，也因为改了会破坏证据签名。

### 4.2 核心研究假设

> 在**有噪声的失败信号**下，CAM 的三个机制能让最终技能质量比原版 SkillOpt 更稳、更好；信号干净时差距缩小。

- **Paired Bootstrap Gate**（`cam/bootstrap_gate.py:47-106`）：对同一批 selection 样本的**成对分差**做 bootstrap，LCB/UCB 三分决策 `accept` / `reject` / `re_evaluate`，替代原版标量门控。**注意 `re_evaluate` 是终态**（无消费者，`automatic_retry=false`），实际等于"软拒绝"——状态不变，且**不写 Memory**。
- **Adaptive Budget**（`cam/adaptive_budget.py:22-78`）：失败模式归一化熵 → 置信度 `1 - H/log K` → 预算 `min + conf*(max-min)`。
- **Rejected Memory**（`cam/rejected_memory.py`）：持久化被拒编辑，按 `similarity × |Δscore|` 检索 top-k 注入后续反射。**只在 `reject` 时写入**，`re_evaluate` 不写。

### 4.3 实验矩阵

主 benchmark：**SpreadsheetBench Verified 400**（硬评分，逐单元格比对）

| 方法 | 配置 | 模型对 (optimizer, target) | seed |
| --- | --- | --- | --- |
| Static | `eval_only.py` 不训练 | (`glm`,`glm`) / (`glm`,`qwen`) | 42 |
| SkillOpt 基线 | `cam.enabled=false` | (`glm`,`glm`) / (`glm`,`qwen`) | 42 |
| **CAM-Full** | 三机制全开 | (`glm`,`glm`) / (`glm`,`qwen`) | 42 |
| CAM-No-Bootstrap | `cam.bootstrap_gate=false` | (`glm`,`qwen`) | 42 |
| CAM-No-Adaptive-Budget | `cam.adaptive_budget=false` | (`glm`,`qwen`) | 42 |
| CAM-No-Memory | `cam.memory_enabled=false` | (`glm`,`qwen`) | 42 |

- 训练单元：3 方法 × 2 模型对 = **6 个**；Static 评测 **2 个**；消融 **3 个** → **合计 11 个**
- 副 benchmark **LiveMathematicianBench**（177 项，2.4 MB，多选题）：Static / SkillOpt / CAM-Full × 弱 target = **3 个**

**总计 14 个单元。**

### 4.4 规模与预算（已对齐上游协议）

**重要修正**：初稿把 `batch_size` 写成 20（导致步数翻倍）、`analyst_workers` 写成 8，两处都是无科学理由地偏离上游。**已改回上游值**：

| 参数 | 上游（`_base_/default.yaml` + benchmark config） | 本方案 | 一致 |
| --- | --- | --- | --- |
| `num_epochs` | 4 | 4 | ✅ |
| `train_size` | 80 | 80 | ✅ |
| `batch_size` | 40 | 40 | ✅ |
| `learning_rate` | 4 | 4 | ✅ |
| `minibatch_size` | 8 | 8 | ✅ |
| `merge_batch_size` | 8 | 8 | ✅ |
| `max_analyst_rounds` | 3 | 3 | ✅ |
| `analyst_workers` | 16 | 16 | ✅ |
| `slow_update_samples` | 20 | 20 | ✅ |
| `use_meta_skill` | true | true | ✅ |

```text
train_size = 80, batch_size = 40      → 2 step/epoch
num_epochs = 4                        → 8 step   （与上游一致）
rollout           = 8 × 40            = 320
selection eval    = 8 × sel_env_num   （每个新技能 1 次；参考技能命中缓存）
测试评测          = 40 (val) + 280 (test)
```

- `glm`：1–3 s/次 + 3 s 冷却（`codegen_agent.py:617`），并发 8
- `qwen`：3–8 s/次，且 reasoning 吃预算，约为 `glm` 的 2–3 倍

**"规模放大"体现在模型数量与 benchmark 数量上，而不是偷偷改训练超参** —— 后者会让两个变量同时变化，说不清是哪个起作用。

### 4.5 公平性约束（所有被比较的方法之间必须完全一致）

| 分组 | 必须固定的键 |
| --- | --- |
| 数据 | `env.limit`、`env.split_dir`、`env.split_mode`、`env.split_seed`、`env.data_root`、`env.mode/max_turns/exec_timeout/workers` |
| 训练 | `train.num_epochs/train_size/batch_size/accumulation/seed` |
| 梯度 | `gradient.minibatch_size/merge_batch_size/analyst_workers/max_analyst_rounds/failure_only` |
| 优化器 | `optimizer.learning_rate/min_learning_rate/lr_scheduler/skill_update_mode/use_slow_update/slow_update_samples/longitudinal_pair_policy/use_meta_skill/use_skill_aware_reflection` |
| 模型 | `model.optimizer/target`（optimizer 固定 `glm`）、两个 backend、`reasoning_effort` |
| 评测 | `evaluation.use_gate/sel_env_num/test_env_num/eval_test` |
| CAM | `cam.confidence_level/meaningful_improvement/bootstrap_samples/seed/min_budget/max_budget` |

**唯一允许变化的维度：`target` 模型 + `cam.*` 开关。**

> `train_size=N` 必须与**实际加载的 train split 大小**相等，否则 `trainer.py:470-474` 直接报错。所以 train_size=80 是唯一合法值（split 就是 80）。`sel_env_num≤40`、`test_env_num≤280` 才有截断意义。

### 4.6 记录与统计口径

主指标（论文表）：**`summary.test_hard`**（best-on-val 技能在 `valid_unseen` 上）+ `summary.test_delta_hard`（vs `baseline_test_hard`）。
次指标：`final_test_hard`、`best_selection_hard`。

每单元产出：`config.json`、`summary.json`、`history.json`、`runtime_state.json`、`best_skill.md`、`skills/skill_v*.md`、`cam_gate_state.json`、`cam_gate_events.json`、`cam_rejected_memory.json`、`steps/step_XXXX/*`、`selection_eval_baseline/`、`test_eval*/`、`token_accounting/`。

论文表格：

| Task | Target | Method | Seed | Test hard | Δ vs Static | Δ vs SkillOpt | 95% CI | Accepts/Rejects/Skips | Calls | Tokens | Runtime |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: |

---

## 5. 分阶段执行计划

### 阶段 0 — 环境与数据 ✅ 已完成

1. ✅ conda 环境 `skillopt`（Python 3.12.15）+ `openai pyyaml numpy scipy openpyxl pandas tqdm json_repair requests datasets pyarrow`
2. ✅ SpreadsheetBench Verified 400 从 `hf-mirror.com` 下载（14.3 MB）并物化：**train 80 / val 40 / test 280，400 条记录，0 invalid**
3. ✅ LiveMathematicianBench 4 个月度文件（202511–202602）→ **177 题**
4. ✅ SearchQA 确定性生成 split（`searchqa_id_split` 从未提交，见 3.3 注）→ **train 400 / val 200 / test 1400，2000 条 ID 无重复**
5. ✅ `.env` 就位（密钥、UTF-8、HF 镜像、**NO_PROXY**）
6. ⏳ OfficeQA — 待用户授权 gated 数据集

### 阶段 1 — 连通性验证 ✅ 已完成（历史性首次）

修复 3.3.1-H（`eval_only.py` 不加载 `.env`）与 3.3.2（系统代理丢弃校内 TLS）之后：

| 检查项 | 结果 |
| --- | --- |
| 配置解析（点号键覆盖回读） | ✅ PASS，37 项覆盖全部生效 |
| 单题真实评测 | ✅ `status=completed`，代码执行 + 单元格比对跑通 |
| API 可达 | ✅ `openai` SDK 与 `urllib` 均 0.0 s 返回 200 |
| 20 题评测稳定性 | ✅ **20/20 `completed`，0 基础设施错误** |

> 旧实验从未达到此状态（它们全部是 `auth_error`）。

### 阶段 3 — 基线校准 ✅ 已完成（提前于阶段 2）

在 **20 道 val 题**上测裸考基线：

| 考生 | hard | soft | 错误类型分布 | 耗时 |
| --- | ---: | ---: | --- | ---: |
| `glm`（强） | **0.3000** | 0.3000 | `score_mismatch` ×14、`none` ×6 | 642 s |
| `qwen`（弱） | **0.2500** | 0.2500 | `score_mismatch` ×14、`none` ×5、**`execution_error` ×1、`code_syntax_error` ×1** | 452 s |

**两个基线都落在有效区间**（既非 0% 也非接近 100%），说明题目有难度、有提升空间。

**一个重要的计划外发现**：两个考生的**失败模式集中度不同**——
- `glm` 几乎只有 1 类失败（`score_mismatch`）→ 归一化熵 ≈ 0 → **Adaptive Budget 置信度 ≈ 1.0 → 预算拉满**
- `qwen` 有 3 类失败（`score_mismatch` / `execution_error` / `code_syntax_error`）→ 熵更高 → **置信度更低 → 预算更小**

这正是 Adaptive Budget 机制设计时要区分的情形，**两个 target 天然构成对该机制的有效检验**，比原计划预期的更有信息量。

### 阶段 2 — Smoke 训练（下一步）

### 阶段 4 — 正式矩阵

### 阶段 5 — 副 benchmark

### 阶段 6 — 汇总

结果表（含 95% CI）、消融归因、CAM 决策日志分析（gate 拒绝率 / 平均 budget / memory 命中率）、`reports/EXPERIMENT_RESULTS.md`。

---

## 6. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| 弱 target 基线 ≈ 0 或 ≈ 1 | CAM 无信号 | **阶段 3 强制闸门**，先测基线 |
| `qwen` reasoning 吃满预算返回空 | 假失败、污染信号 | `max_completion_tokens ≥ 2048`；阶段 2 显式检查空响应率 |
| `sel_env_num` 与 val 实际大小不符 | 抛 `SelectionEvidenceError`，整跑作废 | 阶段 1 显式校验；`env.limit` 与 `sel_env_num`、`train_size` 三者同步 |
| `--cfg-options` 用平坦键 | **静默跑错实验臂** | 统一用点号键；阶段 1 dry-run 回读生效值 |
| `re_evaluate` 是终态（等于软拒绝） | CAM-Full 可能因保守而更新更少 | 这是**可能的真实结论**，如实记录；补 `confidence_level=0.90` 敏感性 |
| Adaptive budget 饱和 | 与基线的预算差被误读为机制收益 | **已修**：`cam.max_budget = optimizer.learning_rate = 4` |
| 并发限流 | 重试、时间失控 | 从 `workers=2` 压测 |
| 单次 LLM 超时 | rollout 失败 | `env.exec_timeout=600`；`codegen_agent` 自带 3 次重试 |
| 免费额度突变 | 跑一半没钱 | 每阶段先小样确认可调用 |

---

## 7. DSH 侧需要准备什么

### 7.1 结论：不需要安装任何插件

| 实验需要 | DSH 现成能力 |
| --- | --- |
| 运行 Python / conda | `pwsh` 工具 |
| 长跑训练（8–12 h） | 后台任务 + `job_output` |
| 改配置、写报告 | 文件读写工具 |
| 并发实验单元 | 多个后台任务 |
| 自动续跑 | 目标（goal）续轮 |
| 长上下文文档 | 子智能体 / 工作流 |

**改用 DSH 反而让实验更简单**：本实验 target 后端是 `openai_chat`（纯代码生成，无工具、无沙箱），**完全不依赖任何 agent CLI**。旧设计里 `codex_exec` / `claude_code_exec` 那两个"把 target 当编码 agent 跑在沙箱里"的后端，才是需要宿主提供沙箱执行能力的地方 —— 新设计不需要。

### 7.2 建议添加（可选，推荐）

1. **项目内 skill：`dsh-experiment-protocol`** —— 把"怎么跑一个单元 / 怎么验收 / 停止条件"写成 skill，避免每次重述长命令与验收清单。给 DSH 读，不是给 SkillOpt 读。
2. **保留本轮新建的 5 个探针脚本**：`scripts/probe_zgci_{models,identity,params,budget}.py`、`scripts/fetch_zgci_pricing.py`。
3. **密钥只放 `.env`**，任何报告/配置/日志不出现明文 key。

---

## 8. 旧残留清理计划

### 8.1 删除

| 路径 | 内容 |
| --- | --- |
| `reports/stage12/` | 全部旧跑结果、恢复审计、token 账本、健康快照（约 100 个文件，已证全部无效） |
| `reports/stage13_codex_cli_preflight_report.md` | Codex CLI 前置检查 |
| `docs/experiments/CAM_CODEX_CLI_EXPERIMENT_PLAN.md` | 旧实验方案（由本文件取代） |
| `reports/EXPERIMENT_PROGRESS.md` | 重写为新口径 |

### 8.2 保留

- `reports/FILE_COMMIT_NOTES.md` —— 非实验残留
- 全部 `skillopt/` 源码，**包括 `codex_*` 后端代码**
  - 理由：(a) 删代码有回归风险（`codex_exec` 与 `openai_chat` 共用 `codegen_agent` 分支，`tests/` 有 3 个测试文件覆盖）；(b) **改 `trainer.py`/`cam/**` 会破坏 `_cam_signature` 证据签名**。→ 改为**文档标记废弃**，代码保留但不进入任何新配置。
- 本轮新增的 5 个探针脚本

> **需确认**：`reports/stage12/` **直接删除**还是**先打包归档再删**？我默认**直接删除**（已证无效，留着只会污染后续判断）。

---

## 9. 待确认的 4 个决策点

| # | 问题 | 建议 |
| --- | --- | --- |
| 1 | `reports/stage12/` 直接删还是先归档？ | **直接删** |
| 2 | 模型矩阵用 `glm`(强) + `qwen`(弱) 两个 target？ | **是**。`gemma4:26b` 更慢且严格 JSON 不稳；`smart-router*` 身份不可信 |
| 3 | 副 benchmark 跑不跑 LiveMathematicianBench？ | **跑**（2.4 MB，评测快，验证泛化性）。想缩短周期可砍掉（14 h → 12 h） |
| 4 | 添加项目内 skill `dsh-experiment-protocol`？ | **加**，成本低、减少重复沟通 |

批准后按「阶段 0 → 6」执行。**每阶段结束向你汇报验收结果；任一阶段不通过就停下找你，不硬跑。**
