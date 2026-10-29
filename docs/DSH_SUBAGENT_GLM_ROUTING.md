# DSH 子代理路由到内网 GLM 的配置与规则

- 建立时间：2026-10-17
- 目的：**主 agent 继续用 DeepSeek（省不了的部分），子 agent 全部走内网 GLM**，把实验类重活的额度消耗从 DeepSeek 转移到免费的内网网关。
- 状态：**已配置**；对**新会话**生效（DSH 的既有会话保留启动时的插件版本）。

---

## 1. 结果一句话

```
主 agent（本会话我自己）  → deepseek-account / deepseek-flash   （不变）
子 agent（subagent / subagent_fork） → zgci / glm               （新增）
子 agent（workflow 的 agent()）      → zgci / glm               （通过调用参数）
```

---

## 2. 改了什么（两个文件）

### 2.1 `~/.dsh/profiles/desktop/cordis.patch.yml`

新增三个顶层条目：

| 条目 | 作用 |
| --- | --- |
| `llm-pi-ai` → `providers.zgci` | 把内网网关注册成一个 OpenAI 兼容 provider 路由 |
| `subagent-model-selection-settings` | 允许子代理选择的路由白名单（只有 `zgci/glm`） |
| `preset-standard`（整段覆盖） | 在 `tool-subagent` / `tool-subagent-fork` 上注入 `agentOptions`，**强制**子代理用 GLM |

**`agent-default-model` 未改动** —— 主代理仍是 DeepSeek。

### 2.2 `~/.dsh/.credentials.yaml`

新增 `refs:` 段：

```yaml
refs:
  ZGCI_API_KEY: <内网网关 key>
```

凭据解析优先级（`dsh-credentials-local`）：**进程环境变量 > 本文件 refs > .env 回退**。
本文件有文件监听，**改完即时生效**。

---

## 3. 为什么必须整段覆盖 preset（关键坑）

第一次尝试直接给 `id: tool-subagent` 加 `agentOptions` —— **无效**。

原因：`@deepseek-ai/dsh-web-app` 这一层把 Host 平面的
`tool-subagent` / `tool-subagent-fork` **都 `disabled: true` 了**，
真正的子代理工具由 **agent preset**（默认 `standard`）在自己的 `plugins` 里挂载。
所以顶层 patch 打到的是一个被禁用的行。

DSH 自带的 `editing-cordis-compositions` 文档给出的正解是：

> Override the declaration by its Loader row id instead of inserting.
> **The override replaces the complete `config`**, so restate `id`, `plugins`
> and every other field the shipped file carries.

`presets/standard.patch.yml` 里是 `- id: preset-standard`，其 `config.plugins`
有 19 个插件条目。覆盖时必须**完整重述**。

因此用脚本生成，而不是手抄：

```bash
python -X utf8 scripts/build_preset_override.py \
  <shipped standard.patch.yml>  <输出文件>
```

脚本做三件事：读原始 preset → 在 `tool-subagent` / `tool-subagent-fork` 的
`config` 末尾注入 `agentOptions`（保留 `!!js` 表达式与全部注释）→
整体 de-indent 4 格写成顶层覆盖条目。

**已验证**：`!!js` 表达式 2/2 保全；除 `agentOptions` 外与出厂 preset 逐行一致；19 个 plugins 全在。

---

## 4. 我在实验中遵守的规则

> **凡是把具体实验工作交给子代理，一律指定 `provider: zgci, model: glm`。**

具体做法（按可用性排序）：

1. **`subagent` / `subagent_fork`**：已由 `agentOptions` 强制为 GLM，无需额外指定。
2. **`workflow` 的 `agent()`**：该工具的 config 不支持模型覆盖，但 `agent()` 的
   opts 支持运行时覆盖，必须显式写在脚本里：
   ```js
   await agent("...", { label: "...", provider: "zgci", model: "glm" })
   ```
   （实测：不写就用父代理的 DeepSeek。）
3. **绝不让子代理跑在 DeepSeek 上** —— 若有子代理报告自己是 `deepseek-*`，
   说明路由没生效，应停下来查配置而不是继续烧额度。

我自己（主 agent）的对话、规划、决策仍用 DeepSeek —— 这部分省不了，也是用户要保留的。

---

## 5. 生效条件与注意事项

| 项 | 说明 |
| --- | --- |
| **生效范围** | **新会话**。DSH 文档：既有会话及其子会话保留启动时的插件版本，改完要在新会话验证。 |
| **preset 被冻结** | 覆盖条目冻结了出厂 `standard` 的 19 个插件。DSH 升级后若出厂 preset 增加新插件，本覆盖不会自动跟上 —— **升级后重新运行生成脚本**。 |
| **回滚** | 备份在 `.dsh-config-backup/<时间戳>/`。把那里的 `cordis.patch.yml` 拷回 `~/.dsh/profiles/desktop/` 即可（只回滚子代理路由，不动凭据）。 |
| **不要删 `refs.ZGCI_API_KEY`** | 删了子代理会以 `MISSING_CREDENTIAL` 失败。 |
| **网关可用性** | 内网 `hub.zgci.org` 解析到 `10.1.132.25`。Node/pi-ai 直连（不受本机系统代理影响），已验证流式 SSE 与模型发现均正常。 |

---

## 6. 如何验证

**新会话里**让子代理自报模型：

```
用 subagent 起一个子代理，让它只回一句自己是什么模型。
期望：GLM / Z.ai 系；若回 deepseek-flash 就是没生效。
```

**当前会话内**可用的验证方式（workflow 的运行时覆盖与 preset 无关）：

```
workflow: agent("...", { provider: "zgci", model: "glm" })
期望返回 GLM 的自述
```

**查权威证据**：子代理会话日志 `~/.dsh/sessions/--<workspace>--/<child-id>/session.v4.jsonl.zstd`
里记录实际 `model` / `provider`。

---

## 7. 网关的技术规格（写配置时用到的）

| 项 | 值 |
| --- | --- |
| baseURL | `https://hub.zgci.org/v1`（**必须带 `/v1`**） |
| 协议 | `openai-completions` |
| 模型 id | `glm`（平台稳定别名，指向 GLM-5.3） |
| 上下文 | 1,000,000 token |
| 输出上限 | 131,072 token |
| 输入模态 | 仅文本 |
| `reasoningEfforts` | **必须声明 `false`** —— 该网关的 GLM 收到 `reasoning_effort` 会输出崩坏（烧满预算、答案重复） |
| 凭据 | `apiKeyEnv: ZGCI_API_KEY` |

---

## 8. 相关脚本

| 脚本 | 用途 |
| --- | --- |
| `scripts/build_preset_override.py` | 从出厂 preset 生成带 `agentOptions` 的覆盖条目（DSH 升级后重跑） |
| `scripts/asar_tool.py` | 读 Electron `app.asar`（DSH 源码在里面，Shell 打不开） |
| `scripts/probe_zgci_*.py` | 网关能力/参数/思考开关探针 |
