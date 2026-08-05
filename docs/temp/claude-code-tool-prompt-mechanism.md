# Claude Code 工具提示词管理机制

## 一、不是"一次性全部注入"——而是分层加载

Claude Code **不会**将所有工具的完整定义一次性全部塞进 system prompt。它采用了一套分层、按需加载的机制。核心原则：**尽可能晚加载，尽可能少加载**。

### 1. 内置工具（Built-in Tools）——始终完整加载

Read、Edit、Bash、Glob、Grep 等约 30 个核心工具的定义是 system prompt 的一部分，每次会话约消耗 ~2000 tokens。这是 Claude 的"天生能力"，始终可用。

### 2. MCP 工具——按名加载，Schema 延迟获取

- **启动时**：只加载 MCP 工具的名称列表（~120 tokens），相当于一个目录
- **需要时**：通过 `ToolSearch` 机制按需获取某个工具的完整 JSON Schema
- **未用到的工具**：整个会话都不会加载其完整定义

受环境变量 `ENABLE_TOOL_SEARCH` 控制：
- `ENABLE_TOOL_SEARCH=true`（默认）：强制延迟加载
- `ENABLE_TOOL_SEARCH=auto`：阈值模式，所有 MCP 工具 Schema 总和不超过 context 窗口 10% 时一次性加载
- `ENABLE_TOOL_SEARCH=false`：禁用延迟加载，全部在启动时加载

### 3. Skill——描述加载，内容按需加载

- 启动时只加载技能名和简短描述
- 判定相关时才加载完整内容
- `disable-model-invocation: true` 的技能连描述都不加载

### 4. 子代理（Subagent）——完全隔离的 context

子代理有自己的 context window，与主会话隔离。它在子窗口中做大量操作，最终只返回一个摘要给主会话，主会话 context 几乎不受影响。

### 5. Hooks——零 context 成本

PreToolUse/PostToolUse 等钩子在 context 外执行，只有显式返回输出时才进入会话。

---

## 二、工具定义的生命周期

### MCP 工具

```
[未连接] → [已连接，仅名称在 context 中] → [Schema 完整加载] → [被调用，返回结果] → [压缩时被摘要化]
```

### Skill

```
[文件存在] → [描述在 context 中] → [内容按需加载] → [压缩时按预算保留/丢弃]
```

### 内置工具

```
[始终以完整 Schema 存在于 system prompt，永不被丢弃]
```

---

## 三、完整请求处理流程

### 会话启动时的 Context 组装

```
┌─ System Prompt (~4200 tokens) ─────────────────────┐
│  内置工具完整定义：Read, Edit, Bash, Agent, Skill... │
│  行为指令 + 输出格式规则                              │
├─────────────────────────────────────────────────────┤
│  CLAUDE.md + Memory + 环境信息                       │
├─────────────────────────────────────────────────────┤
│  Skill 描述列表（仅名称+简述，不含正文）                │
├─────────────────────────────────────────────────────┤
│  MCP 工具名称列表（仅名称，不含 Schema）  ← ~120 tokens │
└─────────────────────────────────────────────────────┘
```

### 实例：用户说"帮我在 JIRA 查 ABC-123 的状态，然后更新本地代码"

**Step 1 — 用户发送消息**

用户输入被追加入 messages 数组，发给 API。Claude 开始推理。

**Step 2 — Claude 决定用 MCP 工具**

Claude 推理后认为需要查 JIRA，返回的不是完整的工具调用，而是一个**轻量引用**（`tool_reference`），只说"我要用 `jira_get_issue`"。

**Step 3 — 框架拦截，触发懒加载（关键机制）**

```
Claude 返回 tool_reference("jira_get_issue")
        │
        ▼
框架检查：这个工具所属的 MCP server 加载过 Schema 吗？
        │
        ├── 加载过 → 直接用
        │
        └── 没加载过 → 立即调用 MCP server 的 tools/list
                        │
                        ▼
                  拿到该 server 所有工具的完整 JSON Schema
                  （一次拿全部，不是只拿一个）
                        │
                        ▼
                  注入到下一轮 API 请求的 tools 数组中
```

框架调用 `tools/list` 时拿到的是**该 server 的全部工具**。之后 Claude 再用同 server 的其他工具，不需要再加载。

`tool_reference` 机制是框架在后台自动处理的，**对 Claude 透明**——Claude 只感觉"我想用某个工具，下一轮就有完整定义了"。

**Step 4 — Claude 真正调用工具**

第二轮请求中，`jira_get_issue` 的完整 Schema 已经在 tools 数组里。Claude 构造正确参数发起调用 → 框架转译为 MCP 协议的 `tools/call` → 发给 JIRA server → 拿到返回结果 → 追加回 messages。

**Step 5 — Claude 决定加载 Skill**

推理继续。Claude 看到 Skill 列表中有 `code-conventions`，决定需要加载代码规范。它调用内置的 **Skill 工具**：

```
Skill(name="code-conventions")
```

框架拦截到调用：
1. 如果 SKILL.md 中有 `!command` 动态注入指令，先执行 shell 命令
2. 将渲染后的完整 SKILL.md 内容作为一条消息插入对话
3. Claude 在下一轮读取并执行其中的指令/

**Step 6 — 修改代码（内置工具）**

Claude 使用 Read、Edit 等内置工具改代码。这些工具的 Schema 从一开始就在 system prompt 中，**无需任何加载过程**。

**Step 7 — Context 快满了，自动压缩**

当 context 接近 200K 上限时，框架自动触发压缩：

```
保留优先级（从高到低）：

  始终保留 ──┬── System prompt（含内置工具定义）→ 压缩后重新注入
             ├── CLAUDE.md + Memory
             └── 用户请求的意图摘要

  按预算保留 ─├── MCP 工具 Schema → 框架层有内存缓存，压缩后可重新注入
             ├── 已调用的 Skill 内容 → 每个 skill 预算 5000 tokens，共享 25K
             └── 关键代码片段

  优先丢弃 ──┼── 旧的 Bash 输出
             ├── 早期 Read 的文件内容
             └── 中间推理步骤
```

内置工具定义永远不丢（在 system prompt 中，压缩后重新注入）。MCP Schema 在 context 中被清掉后，框架层有内存缓存，Claude 下次再用时自动重新注入，不需重新调 `tools/list`。

**Step 8 — 循环继续，直到完成**

---

## 四、Tool Reference 长什么样

正常加载完整 Schema 时，tools 数组里每个工具：

```json
{
  "name": "jira_get_issue",
  "description": "Get a JIRA issue by its key. Returns title, status, assignee, and description.",
  "input_schema": {
    "type": "object",
    "properties": {
      "issue_key": {
        "type": "string",
        "description": "The JIRA issue key, e.g. PROJECT-123"
      }
    },
    "required": ["issue_key"]
  }
}
```

延迟加载模式下，框架发送轻量引用（`tool_reference` 块），只包含工具名和服务器标识：

```json
{
  "type": "tool_reference",
  "name": "jira_get_issue",
  "server": "jira-mcp-server"
}
```

折算成 token：完整 Schema 200-500 tokens，轻量引用仅 5-10 tokens。

### 实际 API 请求中，tools 数组示例

```json
[
  // ===== 内置工具：始终完整 =====
  { "name": "Read",   "input_schema": {...完整...} },
  { "name": "Edit",   "input_schema": {...完整...} },
  { "name": "Bash",   "input_schema": {...完整...} },
  // ... 约 30 个内置工具，每个几百 tokens

  // ===== MCP 工具：仅引用 =====
  { "type": "tool_reference", "name": "jira_get_issue" },
  { "type": "tool_reference", "name": "jira_search" },
  { "type": "tool_reference", "name": "jira_create" },
  { "type": "tool_reference", "name": "github_list_prs" },
  { "type": "tool_reference", "name": "github_get_diff" }
  // ... 全部 MCP 工具的名册，总计 ~120 tokens
]
```

即便接了 3 个 MCP 服务器、暴露了 20 个工具，启动时额外消耗也只有 ~100-200 tokens，而不是 20 × 500 = 10,000 tokens。

`tool_reference` 是 Anthropic API 的原生特性（beta），需要模型支持（Sonnet 4+ / Opus 4+），因为模型需要能够返回 `tool_reference` 块而不是完整的 `tool_use` 块来表达"我想用这个工具，但我还没有它的 Schema"。

---

## 五、Claude Code 与 Function Calling

### 底层机制

Claude Code 的整个运行机制建立在 **tool use**（Anthropic 的函数调用功能）之上。本质是 API 层的 tool use + 一个外层的执行框架（harness）组成的循环。

每轮 API 请求：

```
messages: [
  { role: "user", content: "帮我读一下 config.py" }
]
tools: [
  { name: "Read",  input_schema: {...} },
  { name: "Edit",  input_schema: {...} },
  { name: "Bash",  input_schema: {...} },
  // ...
]
```

模型返回带有 `tool_use` 块的结构化响应：

```json
{
  "role": "assistant",
  "content": [
    { "type": "text", "text": "让我读取 config.py 的内容。" },
    { "type": "tool_use", "id": "toolu_001", "name": "Read", "input": { "file_path": "/path/to/config.py" } }
  ]
}
```

框架拦截 `tool_use`，执行实际的 Read 操作，把结果包装成 `tool_result` 追加回 messages，再发起下一轮 API 请求：

```json
{
  "role": "user",
  "content": [
    { "type": "tool_result", "tool_use_id": "toolu_001", "content": "...文件内容..." }
  ]
}
```

模型看到结果后继续推理，可能返回文本，也可能再次发起 `tool_use`。这就是 **Agentic Loop**：

```
用户输入 → API(tools=所有工具) → 模型返回 tool_use → 框架执行 → 结果注入 → API(tools=...) → ...
```

一个不断在 tool use 和 tool result 之间循环的 while 循环，直到模型认为任务完成、不再返回 `tool_use` 为止。

Anthropic 管这个叫 **tool use**，OpenAI 管它叫 **function calling**，本质是同一个东西。

### 判断"该用哪个工具"——框架不参与决策

框架只负责：
1. 把可用的工具/Skill **列出来**（名称或描述）
2. 当 Claude 想用时，**按需提供完整定义/内容**

实际判断和决策全部由 Claude 模型自己完成。框架是一个"后勤系统"，不是"调度系统"。

---

## 六、Function Call 准确率问题及 Claude Code 的应对

### 准确率低的主要场景

**1. 复杂 Schema 的参数填充错误**

如果工具定义有十几个嵌套字段，模型容易在字段间跳错、漏填 required、填错嵌套路径。复杂的企业 API（电商、CRM、财务系统）是重灾区。

**2. 多工具混淆**

给模型 50-100 个工具，功能和参数相似（比如 `search_users` vs `list_users` vs `find_members`），模型容易选错。

**3. 幻觉调用**

模型有时会调用不存在的工具，或者编造参数值。

### Claude Code 的规避策略

| 风险点 | Claude Code 的做法 |
|---|---|
| **Schema 复杂度** | 每个工具参数极少（Read 只有 `file_path` + 可选 offset/limit；Bash 只有 `command` + 可选 `description`），几乎不存在"填错字段"的空间 |
| **工具数量** | 约 30 个内置工具，每个语义边界清晰（Read vs Glob vs Grep 功能不重叠），很少混淆 |
| **幻觉调用** | 文件路径、行号等参数由框架在执行时校验——如果 Read 了一个不存在的文件，框架返回错误 `tool_result`，模型看到错误后自动纠错 |
| **迭代纠错** | Agentic Loop 天然支持重试——工具执行失败 → 错误信息返回模型 → 模型读到错误 → 修正参数再调一次 |

Claude Code 的工具体系是 **"宽进严出"**：允许模型大胆调用，靠执行层的错误反馈来兜底。这和传统 function calling 场景（期望一次调用就完美的 RAG pipeline 等）不同。

此外，Claude 3 开始 tool use 就是强项，到 Claude 4 系列已经非常成熟。加上 `tool_reference` 机制让模型能"先要 Schema 再调用"而不是"盲猜 Schema"，进一步降低了出错率。

---

## 七、不同权限模式下的工具策略

Plan Mode 等模式**不是靠不注入工具定义**来限制能力的。工具定义始终在 system prompt 中，但通过**权限系统**标记某些工具不可用——Claude 知道那些工具存在，只是调不了。

| 模式 | 可用的工具 | 限制机制 |
|---|---|---|
| **Default（默认）** | 全部工具，但文件编辑和 shell 命令需要确认 | 权限提示 |
| **AcceptEdits** | 全部工具，文件操作自动接受 | 权限提示简化 |
| **Plan Mode（计划模式）** | 只读工具（Read、Glob、Grep、WebSearch 等），禁止修改 | 权限规则限制 |
| **Auto Mode** | 全部工具，自动判断权限 | 后台安全检查 |

---

## 八、工具相关配置

### ENABLE_TOOL_SEARCH 环境变量

- `true`（默认）：所有 MCP 工具延迟加载
- `auto`：所有 MCP 工具 Schema 总和不超过 context 窗口 10% 时一次性加载，否则延迟加载
- `false`：禁用延迟加载，全部在启动时加载

### MCP Server 的 alwaysLoad 配置

如果 MCP server 配置了 `"alwaysLoad": true`，则它的所有工具 Schema 在会话启动时就完整加载，不经过延迟加载。框架会等待该服务器连接（最长 5 秒超时）后再构建第一个 prompt。

### Skill 描述预算

所有 Skill 描述的字符数预算为模型 context 窗口大小的 1%。如果溢出，最不经常调用的 Skill 的描述会被优先裁断（只保留名称），常用 Skill 保留完整描述。可通过 `skillListingBudgetFraction` 调整。

### Skill 可见性控制

- `disable-model-invocation: true`：Skill 描述完全不出现在 context 中，只有用户手动通过 `/skill-name` 触发
- `user-invocable: false`：Skill 不在 `/` 菜单中出现，但 Claude 可以根据上下文自动调用
- `skillOverrides` setting：从 settings.json 控制每个 skill 的可见性状态（on / name-only / user-invocable-only / off）