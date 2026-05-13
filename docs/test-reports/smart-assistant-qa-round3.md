# Smart Assistant (AI助手) — 第三轮 QA 全面审查报告

**日期**: 2026-05-13
**审查范围**: ~56 源文件, 4 个 Plan, 60+ 工具, 7 个 Agent
**审查方式**: 4 维度并行审查 (功能/安全/性能/代码质量) → 汇总去重
**上一轮基准**: QA Fix 报告 (2026-05-12), 评分 ~51/60

---

## 总览

| 严重级别 | 原报告 | 本轮新发现 | 去重后 | 说明 |
|---------|--------|-----------|--------|------|
| **Blocker** | 3 (已修复) | 3 | 3 | 观测数据丢失 + 系统提示替换 + 测试无法加载 |
| **Critical** | 10 (已修复) | 8 | 6 | MCP认证无效 + 流式重建 + 双定义死代码 + AgentWorker绕过护栏等 |
| **Major** | 16 (已修复) | 14 | 11 | 同步FAISS + 自动模式跳过确认 + search_memory缺失 + 静默异常吞没等 |
| **Minor** | 21 (17修复) | 14 | 12 | 命名/路径泄露/明文密钥/魔法数字/工具泄漏等 |

**综合评分**: 功能 44/60 · 安全 42/60 · 性能 30/60 · 代码质量 27/60 → **平均 36/60**

---

## Blocker 级问题

### BR1. test_agent_tool_integration.py 导入不存在的 `_filter_entries` → 全部集成测试无法加载

**发现维度**: 代码质量 (BR1) + 功能 (M1)
**严重级别**: Blocker

`tests/test_agent_tool_integration.py:21` 导入 `_filter_entries`（带前导下划线），但 `tools/base.py:254` 中实际函数名为 `filter_entries`（无下划线）。QA Fix 的 m1 修复将函数改名时未同步更新测试文件。整个集成测试套件 (~89 用例，含 TestFullWorkflowChain/TestLabelSystem/TestSecurityGuardrails/TestTranslationConfig 等) 在模块加载阶段即因 `ImportError` 全部失败。

**涉及文件**: `tests/test_agent_tool_integration.py:21`, `tools/base.py:254`

**修复**: 将测试中所有 `_filter_entries` 改为 `filter_entries`

---

### BR2. ObservabilityCollector.end_conversation() 在保存前清空 tools_called → 工具调用记录永不被持久化

**发现维度**: 功能 (B1)
**严重级别**: Blocker

`observability/collector.py:68-70`:
```python
self._active.tools_called.clear()  # ← 先清空
trace = self._active               # ← 再赋值 (同一对象引用，已为空)
self._active = None
self._save_trace(trace)            # ← 保存空列表
```
`trace` 和 `self._active` 是同一对象引用，`clear()` 后 `trace.tools_called` 已为空列表。观测系统**永不能**持久化任何工具调用记录。m15 修复声称"对话结束时重置活跃追踪"但实现错误——应先保存后清理。

**涉及文件**: `observability/collector.py:68-70`

---

### BR3. 记忆上下文注入替换系统提示词 → LLM 丢失工具访问

**发现维度**: 功能 (B2)
**严重级别**: Blocker

`chat_widget.py:682-688` 中 `_on_send` 先调用 `add_system(memory_context)` —— 这会**替换**完整的系统提示词（含工具 schema、执行策略、行为指令）。随后 `_run_llm_round:291` 检查到已有 system 消息便跳过添加真实系统提示。结果：LLM 仅收到记忆上下文作为系统指令，**丢失所有工具描述和执行策略**。

**涉及文件**: `chat_widget.py:682-688, 291-297`

---

## Critical 级问题

### CR1. MCP auth_token 无法配置 → 认证机制为死代码

**发现维度**: 安全 (C7 修复无效)
**严重级别**: Critical

`mcp/server.py:52-60` 的 `_authenticate()` 从 `self._config["auth_token"]` 读取令牌，但 `LLMConfig` (config/llm.py) 的 `[mcp]` 节仅包含 `enabled`/`transport`/`admin_tool_whitelist`/`write_tool_policy` —— **无 `auth_token` 字段**。令牌始终为空字符串，`_authenticate` 无条件返回 `True`。MCP 认证机制形同虚设。

**涉及文件**: `mcp/server.py:52-60`, `config/llm.py` (MCP 段定义)

---

### CR2. 流式气泡每次 chunk 销毁并重建完整 QWidget 层级 → UI 抖动

**发现维度**: 性能 (S1) + 功能 (C1)
**严重级别**: Critical

`chat_widget.py:318-335` 的 `_flush_streaming()` 每次 50ms 定时器触发时:
1. 找到旧气泡 → `removeWidget()` + `deleteLater()`
2. 创建全新 `MessageBubble(self._streaming_text, "assistant")`
3. 新 MessageBubble 内部调用 `MarkdownRenderer.render()` 创建 3-15 个 QWidget

50 个 chunk 的流式响应 = 150-750 个 QWidget 被创建和销毁。QA Fix 的 m17 明确要求"复用组件"但未正确实现。

**涉及文件**: `chat_widget.py:318-335`, `message_bubble.py:59`

---

### CR3. `require_collection` 在 base.py 中定义两次 → 第一个定义为死代码

**发现维度**: 代码质量 (CR1)
**严重级别**: Critical

`tools/base.py:236-249` 和 `tools/base.py:300-314` 各有一个 `require_collection` 定义。第一个实现使用 `getattr(ctx, 'collection', None)` 检查，第二个使用 `ctx.active_slot.collection` 回退到 `ctx.collection`。第一个定义**从未被导入或调用**——所有导入方都使用第二个。造成代码阅读理解混乱。

**涉及文件**: `tools/base.py:236-249` (应删除)

---

### CR4. GraphExecutor ABC 孤立无引用 → 死抽象类

**发现维度**: 代码质量 (CR2)
**严重级别**: Critical

`graph_executor.py` 定义了 `GraphExecutor` ABC（含 `execute_graph`/`cancel`/`pause`/`resume` 抽象方法），但 `ExecutionEngine` **没有**继承它。全代码库无任何 `GraphExecutor` 导入。`__init__.py` 导出它给人以正式接口契约的假象。

**涉及文件**: `graph_executor.py`, `execution_engine.py` (未继承)

---

### CR5. AgentWorker 完全绕过护栏链 → 安全缺口

**发现维度**: 代码质量 (MA1)
**严重级别**: Critical

`agents/agent_worker.py:43` 中 `tool.execute()` **直接调用**，跳过 `execute_with_guardrails()` 的完整中间件链 (PermissionGuard → InputValidationGuard → OutputValidationGuard)。这意味着 Agent 调度的工具调用不受权限检查、路径遍历检测、输出脱敏保护。这与原 B1 Blocker 同类——ReAct 路径已修复，Agent 路径仍为裸调用。

**涉及文件**: `agents/agent_worker.py:36-43`

---

### CR6. ReAct 模式从不调用 end_conversation() → 观测数据丢失

**发现维度**: 功能 (C2)
**严重级别**: Critical

`ObservabilityCollector.end_conversation()` 仅在 `_on_plan_all_finished`（Plan 模式）中调用。ReAct 模式（LLM 返回纯文本或达到最大深度时）从不正式结束对话。ReAct 会话的追踪数据永不被持久化，token 统计和工具记录跨轮次累积无边界。

**涉及文件**: `chat_widget.py` (ReAct 终止路径缺少 end_conversation 调用)

---

## Major 级问题

### MA1. 自动模式跳过 require_confirmation=True 的 write 工具

**发现维度**: 功能 (M3) + 安全 (Major #2)
**严重级别**: Major

`chat_widget.py:611-635` 的 `_auto_execute_steps` 仅检查 `permission == "admin"` 来决定是否回退确认。`require_confirmation=True` 但 `permission="write"` 的工具（如 `stop_task`、`set_translation_config`、`upload_entries`）在自动模式下静默执行。

**涉及文件**: `chat_widget.py:611-635`

---

### MA2. MemoryStore.add() 在调用线程同步执行 FAISS 操作

**发现维度**: 性能 (M1)
**严重级别**: Major

`memory_store.py:148-150` 中 FAISS `IndexFlatIP.add()` 在调用线程同步执行。虽然 JSON 元数据已改为 MemoryWriterThread 异步写入（C9 修复），但向量索引更新仍阻塞调用线程。若从 UI 线程调用（如 `_on_llm_finished:374-380`），在 `embedding_mode="api"` 或 `"local"` 时导致 UI 冻结。

**涉及文件**: `memory_store.py:148-150`

---

### MA3. AgentWorker.cancel() 无法中断运行中的工具执行

**发现维度**: 性能 (M2)
**严重级别**: Major

`agent_worker.py:36-43` 中 `cancel()` 设置 `_cancelled` 标志，`run()` 在执行工具前检查此标志——但 `tool.execute()` 是同步阻塞调用。对于 `is_long_running=True` 的工具（`start_translation`、`write_back`），无法在工具执行期间响应取消请求。

**涉及文件**: `agents/agent_worker.py:36-43`

---

### MA4. TaskManager 单例在会话间泄漏 → reset() 存在但从未被调用

**发现维度**: 性能 (M3)
**严重级别**: Major

`task_manager.py:156-167` 的 `reset()` 类方法可清理所有任务和线程，但**代码库中无任何地方调用它**。面板关闭或清除对话时不重置 TaskManager。处于 "running" 状态的任务句柄和线程引用在会话结束后无限期保留。

**涉及文件**: `task_manager.py:156-167`, `panel.py` (未调用 reset), `chat_widget.py` (未调用 reset)

---

### MA5. `search_memory` 工具不存在 → 上传文件内容不可被 LLM 使用

**发现维度**: 安全 (Major #3)
**严重级别**: Major

`context_builder.py:55` 的系统提示告知 LLM "使用 search_memory 工具检索文件内容"，但该工具从未在 ToolRegistry 中注册。LLM 调用 `search_memory` 时收到"未知工具"错误。上传的文件虽然安全隔离（C6 修复生效），但功能上 LLM 无法访问。

**涉及文件**: `context_builder.py:55`, `tool_registry.py`

---

### MA6. `write_to_strings` 路径校验后未使用 → 校验形同虚设

**发现维度**: 安全 (Major #4)
**严重级别**: Major

`tool_writer.py:81-104` 中 `_tool_write_to_strings` 校验来自 `args.get("path")` 的路径后，调用 `writer.write(None)` —— **将 None 而非校验后的 path 传递给写入器**。用户无法通过参数指定输出位置。

**涉及文件**: `tools/tool_writer.py:81-104`

---

### MA7. token 统计信号永不被触发 → 观测面板显示全零

**发现维度**: 功能 (M4)
**严重级别**: Major

`ObservabilityCollector.on_llm_tokens()` 方法 (collector.py:56) 有定义且 `ChatWidget` 已连接信号，但 **ChatWorker 无 `token_usage` 信号**。全代码库无任何代码发射 `token_stats_updated` 信号。观测面板的 Token 仪表盘永远显示 0。

**涉及文件**: `chat_worker.py`, `observability/collector.py:56`

---

### MA8. 7 处静默 `except Exception: pass` → 问题难以追踪

**发现维度**: 代码质量 (MA5)
**严重级别**: Major

涉及文件:
| 文件:行 | 上下文 |
|---------|--------|
| `tool_translator.py:133` | polish 逐条目错误隐藏 |
| `tool_translator.py:268` | 术语数据库加载错误隐藏 |
| `tool_translator.py:280` | ParaTranz 配置错误隐藏 |
| `tool_default.py:33` | ParaTranz 配置错误隐藏 |
| `tool_default.py:144` | workspace 错误隐藏 |
| `tool_paratranz.py:90` | 逐条目上传错误隐藏 |
| `observability/collector.py:95` | 清理错误隐藏 |

应至少使用 `logger.warning()` 或 `logger.exception()` 记录。

---

### MA9. 记忆检索无 embedding_client → 只能关键词精确匹配

**发现维度**: 功能 (m5)
**严重级别**: Major

`chat_widget.py:62-65` 创建 `MemoryRetriever(self._memory_store)` 时未传入 `embedding_client`。缺失时 `retrieve()` 始终使用精确搜索（关键词匹配），永不用语义搜索。记忆召回能力被削弱到逐字匹配。

**涉及文件**: `chat_widget.py:62-65`, `memory/memory_retriever.py`

---

### MA10. ConversationManager._trim() 仅在 add_user() 触发 → ReAct 可超预算

**发现维度**: 功能 (m8)
**严重级别**: Major

`_trim()` 仅在 `add_user()` 时调用。ReAct 模式下大量 `add_observation()` 调用可在两次用户消息间积累远超 `max_turns` 的消息，token 超预算。

**涉及文件**: `conversation_manager.py:61-95`

---

### MA11. start_translation 未检查术语数据库配置前置条件

**发现维度**: 功能 (m3)
**严重级别**: Major

C3 修复要求 `start_translation` 检查 API Key + Collection + **术语数据库配置**。当前仅检查 API Key，Collection 通过 `@require_collection` 检查。术语数据库来源配置未被检查。

**涉及文件**: `tools/tool_translator.py:26-36`

---

## Minor 级问题 (去重后 12 项)

| # | 发现维度 | 问题 | 文件:行 |
|---|---------|------|---------|
| m1 | 安全 | INI 文件以明文存储 API 密钥 (api_key/embedding_api_key/token) | `config/paths.py`, `config/llm.py` |
| m2 | 安全 | `list_collections` 暴露绝对路径 (m21 修复遗漏) | `tools/tool_default.py:57` |
| m3 | 安全 | 护栏可通过 INI 配置禁用 (enable_input_validation/enable_output_validation) | `config/llm.py:144-150` |
| m4 | 安全 | `get_translation_config` 暴露 base_url 主机名 | `tools/tool_translator.py:295` |
| m5 | 性能 | ToolSpec.max_output_size 字段定义但**无代码读取** | `tool_registry.py:16` |
| m6 | 性能 | RetryHandler 同步 LLM 调用阻塞线程池工作线程 | `retry_handler.py:23-51`, `execution_engine.py:148` |
| m7 | 性能 | LRU 淘汰使用 O(n) `list.pop(0)` → 应用 `collections.deque` | `memory_store.py:199-206` |
| m8 | 性能 | `add_system()` 为每个系统消息重建完整列表副本 O(n) | `conversation_manager.py:17` |
| m9 | 代码质量 | ExecutionEngine._MAX_WORKERS=4 + 确认超时 300s 为硬编码魔法数字 | `execution_engine.py:32, 109` |
| m10 | 代码质量 | execute_graph() (~130行) 和 _eval_ast_node() (~80行) 为上帝方法 | `execution_engine.py:194-319, 362-441` |
| m11 | 代码质量 | Orchestrator/map_to_steps 缺少类型标注 | `agents/orchestrator.py` |
| m12 | 代码质量 | _on_skill 应标记删除但仍存在并连接 (m3 修复未生效) | `chat_widget.py:121` |

---

## 已验证修复 (上一轮 50 项修复确认)

### 完全修复 ✅

| 原 ID | 问题 | 验证 |
|--------|------|------|
| B1 | ReAct 绕过护栏 | `_on_tool_executed` 调用 `execute_with_guardrails()` + `_ensure_middlewares()` ✅ |
| B3 | ExecutionEngine 忽略 middlewares | `__init__` 正确使用传入的 `middlewares` 参数 ✅ |
| C6 | 文件内容直接注入系统提示 | 仅注入文件名+字符数摘要 ✅ |
| C8 | v1 工具无路径校验 | `_tool_write_back`/`_tool_export_json` 调用 `_validate_output_path()` ✅ |
| C3 | API Key 前置条件 | API Key + collection 已检查 ✅ |
| C4/C5 | 配置虚假属性 | pp_* 字段和 paratranz_configured 正确返回 ✅ |
| C10 | ToolResult 无错误分类 | error_category/code/recovery_action 已添加 ✅ |
| M8 | _paused 实例共享 | 改为实例属性 (`threading.Event()` in `__init__`) ✅ |
| M9 | MemoryStore 无大小限制 | MAX_ENTRIES_DEFAULT=1000 + LRU 淘汰 ✅ |
| M10 | _trim 不裁剪 observation | 按轮次裁剪含 observation 和 plan_result ✅ |
| M11 | 无 Token 预算 | `build_tool_schema_for_prompt(namespace)` 按命名空间过滤 ✅ |
| M12 | observation 无限增长 | `add_observation()` 截断 2000 字符 ✅ |
| M13 | 面板关闭不终止线程 | `closeEvent` 覆盖 + worker/engine cancel ✅ |
| M14 | _clear_conversation 不取消 worker | worker/engine 取消 + _uploaded_docs 清理 ✅ |
| M16 | 输入校验过于激进 | _INJECTION_PATTERNS 已放宽 ✅ |
| M4/M5 | Prompt 无工作流/错误恢复 | 系统提示包含完整翻译工序和错误恢复策略 ✅ |
| M1 | RetryHandler 死代码 | ExecutionEngine 中实例化 ✅ |
| M6 | ReAct 无重试 | `_on_tool_executed` 接入 RetryHandler ✅ |

### 部分修复 ⚠️

| 原 ID | 问题 | 状态 |
|--------|------|------|
| C7 | MCP 无认证 | auth_token 存在但 INI 无对应字段 → **CR1** |
| C9 | 记忆持久化 UI 线程 I/O | JSON 异步化但 FAISS 仍同步 → **MA2** |
| M7 | AgentWorker.cancel() 空操作 | 执行前检查取消标志但无法中断运行中工具 → **MA3** |
| M2 | v1/namespace 工具重复 | v1 标记 deprecated 但不从 schema 排除 → 已修复 |
| M3 | collection-is-None 检查去重 | 大部分已用 @require_collection，proofreader 例外 |
| m17 | MarkdownRenderer 组件复用 | 未实现，每次创建新 QWidget → **CR2** |

---

## 各维度评分

| 维度 | 修复前 | QA Fix后 | 本轮 | 关键短板 |
|------|--------|---------|------|---------|
| **功能正确性** | 32/60 | ~52/60 | 44/60 | 观测数据丢失 (BR2) + 系统提示替换 (BR3) + token统计为空 (MA7) |
| **安全性** | 25/60 | ~50/60 | 42/60 | MCP认证无效 (CR1) + AgentWorker绕过护栏 (CR5) + 自动模式跳过确认 (MA1) |
| **性能** | 35/60 | ~48/60 | 30/60 | 流式重建 (CR2) + 同步FAISS (MA2) + TaskManager泄漏 (MA4) |
| **代码质量** | 35/60 | ~52/60 | 27/60 | 测试无法加载 (BR1) + 双定义 (CR3) + 孤立ABC (CR4) + 静默异常 (MA8) |
| **平均** | **32/60** | **~51/60** | **36/60** | |

---

## 修复优先级建议

### 紧急 (Blocker — 必须立即修复)

1. **修复 test_agent_tool_integration.py 导入** (BR1) — `_filter_entries` → `filter_entries`
2. **修复 ObservabilityCollector.end_conversation()** (BR2) — 先保存后清理
3. **修复记忆上下文注入** (BR3) — 合并而非替换系统提示词

### 高优 (Critical — 本周修复)

4. MCP auth_token 可通过 INI 配置 (CR1)
5. 流式气泡改为就地更新内容而非重建 QWidget (CR2)
6. 删除 base.py 中重复的 require_collection 定义 (CR3)
7. AgentWorker 接入 execute_with_guardrails() (CR5)
8. ReAct 模式结束时调用 end_conversation() (CR6)

### 中优 (Major — 本月修复)

9. 自动模式同时检查 require_confirmation (MA1)
10. MemoryStore FAISS 操作异步化 (MA2)
11. AgentWorker 支持中断运行中工具 (MA3)
12. ChatWidget/panel 关闭时调用 TaskManager.reset() (MA4)
13. 注册 search_memory 工具或移除提示 (MA5)
14. ChatWorker 添加 token_usage 信号 (MA7)
15. 替换静默 `except: pass` 为 logger (MA8)
16. MemoryRetriever 传入 embedding_client (MA9)

---

## Plan 合规性摘要

### llm-chat (智能助手面板)
| Story | 状态 | 说明 |
|-------|------|------|
| Story-01~04 | ✅ | 验收通过 |
| Story-05 (体验优化) | ⚠ | QSettings 正常，但流式重建影响体验 (CR2) |
| Story-06/07 (代码分层) | ✅ | ADR-008 合规 (C1 已修复) |
| Story-08-1 (Markdown) | ✅ | 已实现但未被 smart_assistant 导入引用 |
| Story-08-2 (视觉) | ✅ | MessageBubble 已使用 MarkdownRenderer |
| Story-08-3 (布局) | ✅ | 观测折叠、滚到底部按钮 |
| Story-08-4 (流式+自动) | ⚠ | 流式重建 (CR2) + 自动模式跳过确认 (MA1) |

### agent-upgrade (Agent 框架)
| Phase | 状态 | 说明 |
|-------|------|------|
| Phase 1 (infra/Skill/文件/记忆/自纠错) | ⚠ | 记忆检索缺 embedding_client (MA9) |
| Phase 2 P0 (Agent/护栏) | ⚠ | AgentWorker 绕过护栏 (CR5) |
| Phase 2 P1 (Graph/Checkpoint) | ⚠ | GraphExecutor 孤立 (CR4) + execute_graph 上帝方法 (m10) |
| Phase 2 P2 (MCP/可观测) | ⚠ | MCP 认证无效 (CR1) + token 统计为空 (MA7) |

### agent-tool-expansion (工具扩展)
| Story | 状态 | 说明 |
|-------|------|------|
| Story-01~08, 10~12 | ✅ | 工具功能完整 |
| Story-09 (翻译配置) | ✅ | 配置正确 (C4/C5 已修复) |
| Story-13 (Agent 集成) | ⚠ | AgentWorker 绕过护栏 (CR5) |
| Story-14 (集成测试) | ❌ | 测试无法加载 (BR1) |

### smart-assistant-qa-fix (QA 修复)
| Story | 状态 | 说明 |
|-------|------|------|
| Story-01/02 (Blocker) | ✅ | 通过 |
| Story-03 (安全加固) | ⚠ | C7 MCP 认证无效 |
| Story-04 (配置) | ⚠ | term DB 前置条件遗漏 |
| Story-05 (线程资源) | ⚠ | FAISS 未异步 + m17 复用未实现 |
| Story-06 (代码清理) | ⚠ | 双定义 + _on_skill 未删除 + test import 未更新 |
| Story-07 (测试) | ❌ | 集成测试无法加载 (BR1) |

---

## 签名

**QA 审查结论**: ⚠ **需修复 — 3 Blocker + 6 Critical + 11 Major + 12 Minor**

第二轮 QA Fix 报告声称 46/50 修复完成 (51/60)，但本轮深入审查发现：
- **3 个修复不完整或无效**：C7 (MCP 认证)、C9 (FAISS 仍同步)、m17 (组件未复用)
- **2 个修复引入新问题**：m1 (改名导致测试断裂 BR1)、m15 (tools_called 清理顺序错误 BR2)
- **1 个修复未实际执行**：m3 (_on_skill 仍在)
- **5 个新发现严重问题**：AgentWorker 绕过护栏 (CR5)、流式重建 (CR2)、双定义 (CR3)、记忆替换系统提示 (BR3)、token 统计为空 (MA7)

在 Blocker 和 Critical 问题修复前，综合评分不升反降 (51→36)。建议按优先级逐项修复后重新审查。

**审查维度**: 功能测试 / 安全审查 / 性能审查 / 代码质量审查 (4 Agent 并行)
**审查日期**: 2026-05-13
