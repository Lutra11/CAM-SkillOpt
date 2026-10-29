# ZGCI Model Hub 接口与可用模型档案

- 记录时间：2026-10-16
- 用途：CAM-SkillOpt 实验的模型来源档案
- 探测脚本：`scripts/probe_zgci_models.py`、`probe_zgci_identity.py`、`probe_zgci_params.py`、`probe_zgci_budget.py`、`fetch_zgci_pricing.py`
- 原始证据：`reports/probe_zgci_models.json`、`reports/probe_zgci_models_versions.json`、`reports/probe_zgci_identity.json`、`reports/zgci_pricing_dump.txt`

---

## 1. 接口信息

| 项 | 值 |
| --- | --- |
| 网关类型 | New API（one-api 派生） |
| 控制台 | `https://hub.zgci.org` |
| 定价页 | `https://hub.zgci.org/pricing` |
| API Base URL | `https://hub.zgci.org/v1` |
| Chat 端点 | `POST https://hub.zgci.org/v1/chat/completions` |
| 模型列表端点 | `GET https://hub.zgci.org/v1/models` |
| 定价 JSON 端点 | `GET https://hub.zgci.org/api/pricing` |
| 鉴权 | `Authorization: Bearer <API_KEY>` |
| 协议 | OpenAI Chat Completions 兼容 |
| 当前 key 分组 | `oc`（OpenClaw） |
| 分组倍率 | `group_ratio` 全部 = 1 |

### 1.1 API Key

以环境变量方式提供，**不写入仓库**：

```dotenv
OPTIMIZER_AZURE_OPENAI_API_KEY=sk-...
TARGET_AZURE_OPENAI_API_KEY=sk-...
```

`scripts/train.py` 会自动加载项目根目录 `.env`（该文件已在 `.gitignore`）。

> 探测脚本用 `ZGCI_API_KEY` 环境变量读取，同样不落盘：
> ```powershell
> $env:ZGCI_API_KEY = "sk-..."
> python scripts/probe_zgci_models.py
> ```

---

## 2. 费用

`GET /api/pricing` 返回的全部 17 个模型：

```text
model_ratio = 0     model_price = 0     completion_ratio = 1
```

**该 key 调用所有模型均免费（0 倍率）。** 本实验不存在成本约束，唯一约束是时间与并发。

定价页是 JS 渲染的：直接 `web_fetch` 抓 HTML 只能拿到 `You need to enable JavaScript to run this app.`，必须读 `/api/pricing` 的 JSON。

---

## 3. 可用模型清单（`GET /v1/models`，17 个）

### 3.1 文本对话模型

| id | 真实后端 | vendor | 支持端点 | 可用分组 |
| --- | --- | --- | --- | --- |
| `glm` | `glm-5.3-private-19428658` | 智谱(1) | openai | ld, oc, default, app |
| `glm-anthropic` | `glm-5.3-private-19428658` | 智谱(1) | openai | oc, default, app, ld |
| `qwen` | `Qwen3.6-35B-A3B` | 通义千问(2) | openai | edu, app, ld, oc, default |
| `minimax` | `Qwen3.6-35B-A3B` | — | openai | app, ld, oc, default, edu |
| `gemma4:26b` | `gemma4:26b` | — | openai | oc, ld, edu, app, default |
| `smart-router` | 自报 `smart-router` | — | openai | 全部 |
| `smart-router-v2` | 自报 `glm-5.3-private-19428658` | — | openai | ld, oc, default, app |

### 3.2 非对话模型（本实验不使用）

| id | 类型 |
| --- | --- |
| `bge-m3` | 文本向量 |
| `bge-reranker-v2-m3` | 重排 |
| `qwen3-embedding:4b` | 文本向量 |
| `cosy-voice` | 语音合成 |
| `qwen-asr` | 语音识别 |
| `qwen-image` | 图像生成 |
| `minimax-h3` | 视频生成（`openai-video` 端点） |
| `face-detect` | 人脸检测 |
| `face-embed` | 人脸特征 |

---

## 4. 调用约束：只有别名可用

网关按渠道分发，**带版本号的具体模型名一律返回 404**。实测结果：

| 尝试的模型名 | 结果 |
| --- | --- |
| `glm-5.3` | ❌ `model_not_found` — No available channel under group oc |
| `glm-5.3-private-19428658` | ❌ 同上 |
| `glm-4.6` / `glm-4.5` / `glm-5` / `zai/glm-5.3` | ❌ 同上 |
| `Qwen3.6-35B-A3B` / `qwen3.6-35b-a3b` | ❌ 同上 |
| `qwen3-max` / `qwen-max` / `qwen3.5-Plus` | ❌ 同上 |
| `minimax-m2` / `MiniMax-M2.7` / `minimax-m2.7` | ❌ 同上 |
| `deepseek-chat` / `deepseek-v3.2` | ❌ 同上 |
| `kimi-k2` / `kimi-k2-thinking` / `moonshot-v1-128k` | ❌ 同上 |

**结论：只能使用第 3 节表格中 `/v1/models` 公布的别名。**

### 4.1 关于自报身份

向模型询问"你是什么模型"时，只有 `glm` / `glm-anthropic` / `smart-router-v2` 稳定自报为 GLM 系；但 `smart-router` 曾在不同调用中自报为 Kimi 与 GLM。

**因此：`smart-router*` 的身份不可信，不用于需要可复现模型身份的实验对比。** 真实身份以网关响应体的 `model` 字段为准。

---

## 5. 请求参数兼容性（22/22 全部通过）

实测 `glm` 接受以下全部参数，**无需为参数兼容性修改代码**：

```text
max_tokens                 ✅
max_completion_tokens      ✅
reasoning_effort           ✅ (medium / none 均可)
temperature                ✅
top_p                      ✅
n                          ✅
seed                       ✅
stop                       ✅
frequency_penalty          ✅
presence_penalty           ✅
logprobs                   ✅
stream=false               ✅
response_format=json_object ✅
parallel_tool_calls        ✅
enable_thinking            ✅
thinking                   ✅
```

抽样验证（`max_completion_tokens` / `reasoning_effort` 在 `qwen`、`gemma4:26b`、`smart-router-v2` 上同样通过）。

> 注意：网关"接受"不等于"生效"。`reasoning_effort` 对 GLM 的实际效果未验证，实验配置中建议置空以减少变量。

---

## 6. 能力矩阵

| 能力 | `glm` | `qwen` | `minimax` | `gemma4:26b` | `smart-router` | `smart-router-v2` |
| --- | --- | --- | --- | --- | --- | --- |
| 严格 JSON 输出 | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ |
| 函数/工具调用 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Python 代码块 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 身份可信 | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| 首次响应延迟 | 1–3 s | 0.5–2 s | 0.5–2 s | 6–12 s | 0.5–3 s | 1–2 s |

**严格 JSON 测试**：要求只返回 `{"status": "ok", "value": 42}`。`glm` 精确返回；`qwen` 返回空；`gemma4:26b` 返回截断的 `{"status": "ok", "value": 42`（缺右括号）。

**结论**：`glm` 是唯一能胜任 SkillOpt **optimizer**（必须输出可解析的结构化编辑）的模型。

---

## 7. 关键陷阱：空响应

`qwen` / `minimax` 属于 reasoning 型输出，**reasoning 内容与 content 共享 token 预算**。

实测 `qwen`（同一个"写个 clean 函数"的请求）：

| `max_tokens` | `finish_reason` | reasoning 长度 | content 长度 | 结果 |
| --- | --- | --- | --- | --- |
| 256 | `length` | 932 ch | **0 ch** | ❌ **空响应** |
| 2048 | `stop` | 1619 ch | 70 ch | ✅ 正常 |
| 8192 | `stop` | 1538 ch | 70 ch | ✅ 正常 |

**对策：`qwen` 作为 target 时 `max_completion_tokens` 必须 ≥ 2048，建议 16384。**

若配置过小，rollout 会拿到空字符串 → 代码提取失败 → 该题计 0 分。这会**伪装成"模型能力不足"**，实际是配置问题，且会污染 CAM 的失败信号。

`glm` 无此问题：256 tokens 即正常返回（其 reasoning 仅 3–16 ch）。

---

## 8. 网络可达性

| 目标 | 状态 |
| --- | --- |
| `https://hub.zgci.org` | ✅ 可达（解析到内网地址，`web_fetch` 工具拒绝，需本机直连） |
| `https://huggingface.co` | ❌ **阻断**（`基础连接已经关闭`） |
| `https://hf-mirror.com` | ✅ 可达 —— **数据下载必须走镜像** |
| `https://pypi.org` | ✅ 可达 |
| `https://pypi.tuna.tsinghua.edu.cn` | ✅ 可达 |
| `https://github.com` | ✅ 可达 |

---

## 9. 复现命令

```powershell
# 模型清单 + 探活
$env:ZGCI_API_KEY = "sk-..."
python scripts/probe_zgci_models.py --json reports/probe_zgci_models.json

# 别名 → 真实模型映射 + JSON/工具能力
python scripts/probe_zgci_identity.py --repeats 3

# 参数兼容性
python scripts/probe_zgci_params.py

# token 预算 / 空响应边界
python scripts/probe_zgci_budget.py

# 定价与可用模型（直接读 JSON，绕过 JS 渲染）
python scripts/fetch_zgci_pricing.py --out reports/zgci_pricing_dump.txt
```
