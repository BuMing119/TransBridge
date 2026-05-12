# Smart Assistant (AI助手) — 全面 QA 审查报告

**日期**: 2026-05-11
**对应方案**: `plans/llm-chat/plan.md`, `plans/agent-upgrade/plan.md`, `plans/agent-tool-expansion/plan.md`
**审查范围**: ~50 源文件, 3 个 Epic, 60+ 工具, 7 个 Agent
**审查方式**: 4 维度并行审查 (功能/安全/性能/代码质量) → 汇总去重

---

## 总览

| 严重级别 | 数量 | 说明 |
|---------|------|------|
| **Blocker** | 3 | 安全护栏被绕过 + 异步无通知 + 中间件配置失效 |
| **Critical** | 10 | 架构违规 + 测试空白 + 前置条件缺失 + 配置虚假属性 + Prompt注入 + MCP无认证 + v1无校验 + UI线程IO阻塞 |
| **Major** | 16 | 死代码 + 功能重复 + 重复检查 + Prompt无工作流 + 无Token预算 + 线程泄漏 + 内存无上限 |
| **Minor** | 21 | 命名矛盾 + 注册不一致 + 死代码 + 文档缺失 + 流式空实现 + 资源泄漏 |

**综合评分**: 功能 32/60 · 安全 25/60 · 性能 35/60 · 代码质量 35/60 → **平均 32/60**

---

## Blocker 级问题

### B1. ReAct 模式完全绕过所有安全护栏

**发现维度**: 安全 (Blocker #1) + 性能 (Critical #1)
**严重级别**: Blocker

`chat_widget.py:419` 中的 `_on_tool_executed` 直接调用 `spec.execute(step.get("args", {}), self._ctx)`，**完全跳过** `execute_with_guardrails()` 中的完整中间件链 (PermissionGuard → InputValidationGuard → 工具执行 → OutputValidationGuard)。LLM 在 ReAct 模式下可自由调用 admin 级工具 (`write_to_esp`, `write_to_eet`, `write_to_xt`)，无需确认弹窗。路径遍历检测、扩展名白名单、输出脱敏在此路径下全部失效。

同时，该调用在 **UI 线程上同步执行**，对于同步工具 (如 v1 `translate_entries`) 会冻结整个 UI。

**涉及文件**: `chat_widget.py:413-424`, `guardrails/permission.py`, `guardrails/input_validator.py`, `guardrails/output_validator.py`

```python
# chat_widget.py:419 — 当前代码
result = spec.execute(step.get("args", {}), self._ctx)
# 应该改为
result = execute_with_guardrails(spec, step.get("args", {}), self._ctx)
```

---

### B2. 异步翻译/润色任务完成后无通知机制

**发现维度**: 功能 (Blocker #1)
**严重级别**: Blocker

`start_translation` / `start_polish` 启动后台线程后立即返回 `ToolResult.ok("翻译任务已启动", data={"task_id": task_id})`。线程完成时仅调用 `tm.set_status(task_id, "completed")` 然后静默退出。`TaskManager` 是纯 `threading` 类，**无 Qt 信号、无回调、无 on_complete 机制**。

LLM 收到 task_id 后无从得知任务何时完成、结果如何。系统 prompt 也未指导 LLM 需要轮询 `get_task_status`。

**涉及文件**: `task_manager.py:1-136`, `tool_translator.py:83-86`, `chat_widget.py:444-458`

---

### B3. ExecutionEngine 忽略 middlewares 参数

**发现维度**: 安全 (Blocker #2)
**严重级别**: Blocker

`chat_widget.py:340-350` 根据用户配置构建 middlewares 列表并传入 `ExecutionEngine(ToolRegistry, self._ctx, middlewares=middlewares)`。但 `execution_engine.py:41` 中 `self._guards = _build_guard_chain()` 完全**忽略传入的 `middlewares` 参数**（该参数仅传给了 `super().__init__(parent)` 即 QObject）。用户即使关闭了输入校验，该校验仍然生效。虽然表面上"更安全"，但**配置系统不可信**，用户无法按需调整护栏行为。

**涉及文件**: `execution_engine.py:41-44`, `chat_widget.py:340-350`

---

## Critical 级问题

### C1. context_builder.py 从 ui/ 导入 AppContext，违反 ADR-008

**发现维度**: 代码质量 (Critical #1)
**严重级别**: Critical

`smart_assistant/context_builder.py:4` 中 `from src.transbridge.ui.context import AppContext` 违反 ADR-008 "UI → backend 单向依赖" 原则。Backend 不应直接 import UI 模块。

**涉及文件**: `smart_assistant/context_builder.py:4`

---

### C2. 测试覆盖几乎为零

**发现维度**: 代码质量 (Critical #2)
**严重级别**: Critical

Smart Assistant 仅有 **1 个测试文件** (`tests/test_agent_tool_integration.py`, ~1065行)。以下关键模块**零测试覆盖**：
- `ChatWorker` — LLM 流式响应 worker
- `ConversationManager` — 对话状态管理
- `ExecutionEngine.execute()` / `execute_graph()` — DAG 拓扑排序/层级并行/checkpoint
- `RetryHandler` — Reflexion 自纠错
- `MemoryStore` / `MemoryRetriever` — 长期记忆
- `ObservabilityCollector` — 可观测性
- `MarkdownRenderer` — Markdown 渲染器
- `ContextBuilder` — 系统提示构建
- 所有 MCP 模块
- 5 个知识缺口无任何验证测试

**涉及文件**: `tests/test_agent_tool_integration.py` (唯一的测试文件)

---

### C3. start_translation 不检查关键前置条件

**发现维度**: 功能 (Critical #2)
**严重级别**: Critical

`_tool_start_translation` 仅检查 `mode` 合法性和 `collection` 非空。**未检查**:
- API Key 是否已配置
- 术语数据库是否有内容
- 术语来源配置是否就绪
- 后处理开关状态
- 作用域是否合理

LLM 无法通过现有工具判断环境是否就绪。`get_app_state` 也不返回 API 连接状态或配置就绪信息。

**涉及文件**: `tool_translator.py:19-32`, `tool_default.py:15-36`

---

### C4. get_translation_config 使用 getattr 访问不存在属性

**发现维度**: 功能 (Critical #3)
**严重级别**: Critical

`tool_translator.py:226-227` 中:
```python
"post_process_stages": getattr(llm, 'post_process_stages', None),  # ← 永远 None
"term_database": getattr(llm, 'term_database', None),              # ← 永远 None
```
`LLMConfig` 类中**不存在** `post_process_stages` 或 `term_database` 属性。后处理配置实际以 `pp_*` 前缀嵌入 `[llm]` INI 段 (如 `pp_enable_consistency_check`)，但这些字段未被 `get_translation_config` 暴露。

**涉及文件**: `tool_translator.py:226-227`, `config/llm.py`

---

### C5. AI 无法读取 ParaTranz API 配置状态

**发现维度**: 功能 (Critical #4)
**严重级别**: Critical

`paratranz_config.ini` 中的 `[api]` 段 (含 token/URL) 由 `ParatranzConfig` 读取，无任何工具暴露给 LLM。AI 完全不知道 ParaTranz 是否已配置，无法判断 `upload_entries`/`download_entries` 等工具是否可用。

**涉及文件**: `tool_translator.py:210-230`, `config/paratranz.py`

---

### C6. 用户上传文件内容直接注入系统提示词

**发现维度**: 安全 (Critical #3)
**严重级别**: Critical

`context_builder.py:46-48` 将上传文件的 `raw_text[:200]` 直接拼接到系统提示词中。攻击者可上传包含提示注入内容的文件 (如 "忽略之前所有指令...")，劫持 LLM 行为。翻译条目的原文也可作为间接注入向量。

**涉及文件**: `context_builder.py:46-48`, `chat_widget.py:251`

---

### C7. MCP stdio 通道无认证

**发现维度**: 安全 (Critical #4)
**严重级别**: Critical

`mcp/server.py:22` 直接从 `sys.stdin` 读取 JSON-RPC 请求并处理，无任何认证机制。任何能向该进程 stdin 写入的进程都可调用 MCP 暴露的工具。

**涉及文件**: `mcp/server.py:19-38`

---

### C8. v1 工具无路径校验

**发现维度**: 安全 (Critical #5)
**严重级别**: Critical

`tool_v1.py` 中的 `_tool_write_back` (line 118-139) 和 `_tool_export_json` (line 101-115) 直接使用 `ctx.esp_path` 写入文件，**未经过任何路径验证**。相比之下，namespace 工具 (`tool_parser.py`, `tool_writer.py`) 有 `_validate_path` / `_validate_output_path` 检查。

**涉及文件**: `tool_v1.py:118-139, 101-115`

---

### C9. 记忆持久化在 UI 线程上触发同步磁盘 I/O

**发现维度**: 性能 (Critical #2)
**严重级别**: Critical

`chat_widget.py:300` 中 `_on_llm_finished()` 调用 `self._memory_store.add(entry)`，每轮对话触发**两次同步磁盘写入** (JSON 元数据 + FAISS 索引)，全部在 UI 线程上执行。大型记忆库会导致 UI 冻结数秒。

**涉及文件**: `chat_widget.py:294-300`, `memory_store.py:61-63`

---

### C10. ToolResult 无错误分类字段

**发现维度**: 功能 (Major #6 — 升级为 Critical)
**严重级别**: Critical

`ToolResult.fail()` 仅接受 `message: str` 和 `failed_items: list | None`。无 `error_category` / `error_code` / `recovery_action` 字段。RetryHandler 有隐式的 `NON_RETRYABLE` 关键词列表做分类，但分类结果不暴露给调用方或 LLM。

**涉及文件**: `base.py:85-86`, `retry_handler.py:11-14`

---

## Major 级问题

### M1. RetryHandler 定义但从未实例化 — 死代码
`execution_engine.py:39` 中 `self._retry_handler = None`，`execution_engine.py:129-154` 的重试循环因 `self._retry_handler is None` 短路，永远走"立即放弃"分支。`reflexion/retry_handler.py` 的 52 行代码处于死代码状态。

### M2. v1 工具与 namespace 工具功能重复
`tool_v1.py` 的同步工具与 namespace 异步工具存在功能重叠：`_tool_translate_entries` vs `_tool_start_translation`、`_tool_write_back` vs `_tool_write_to_esp`、`_tool_check_quality` vs `tool_proofreader.py` 多个工具。两套系统增加维护负担。

### M3. collection-is-None 检查在 6 个文件中各自实现
`tool_translator.py`, `tool_v1.py`, `tool_writer.py`, `tool_paratranz.py`, `tool_proofreader.py` 各有一套手动检查，而 `tool_editor.py` 已使用 `@require_collection` 装饰器标准化。不一致导致新人困惑。

### M4. 系统提示词完全不包含翻译工作流指导
`prompts.py:5-69` 描述了 plan/React 两种模式的选择规则，但**零覆盖**正确的翻译工序 (确认配置 → 检查术语 → 设作用域 → 预览 → 翻译 → 轮询 → 检查结果 → 后处理 → 写回)。

### M5. 系统提示词无错误恢复策略指导
`prompts.py` 完全不涉及工具执行失败后的恢复策略。LLM 面对相同的错误消息无法区分网络故障 (可重试) 和逻辑错误 (不可重试)。

### M6. RetryHandler 仅在 plan 模式中使用，ReAct 无重试
`execution_engine.py:125-154` 的重试包裹仅在 plan 模式步骤执行中生效。`chat_widget.py:444-458` 的 ReAct 模式 `_handle_tool_result` 完全不使用 RetryHandler。

### M7. AgentWorker.cancel() 是空操作
`agent_worker.py:20-53` 中 `run()` 方法设置了 `_cancelled` 标志但**从未检查**。`cancel()` 调用后 worker 继续运行直到工具完成。

### M8. ExecutionEngine._paused 在实例间共享
`execution_engine.py:179` 中 `_paused: threading.Event` 定义为**类级属性**，所有 ExecutionEngine 实例共享同一个 Event。一个会话暂停会影响另一个会话。

### M9. MemoryStore 无大小限制/淘汰策略
`memory_store.py:54-64` 的 `add()` 无限制地追加到 `_metadata` 字典，每次附加两次同步磁盘写入 (JSON + FAISS)。FAISS 索引中的嵌入向量永久积累 (每个 ~12KB, 1536维)。

### M10. ConversationManager._trim() 不裁剪观察消息
`conversation_manager.py:40-54` 只计算 user+assistant 对来裁剪。工具结果 (observation)、plan_result 消息、孤立 system 消息**永远不会被移除**。有大量工具调用的 ReAct 循环中，消息历史远超 `max_turns=20`。

### M11. ~60 工具无 Token 预算，无截断
`tool_registry.py:59-70` 的 `build_tool_schema_for_prompt()` 输出所有工具的全部参数细节，估计仅工具描述就超过 3000 token。系统无任何 Token 计数或预算机制。长会话中上下文窗口溢出导致静默截断或 API 错误。

### M12. 观察消息无限增长，绕过 max_turns
每个工具调用通过 `add_observation()` 追加 user 角色消息 (含完整结果文本)，全部发送回 LLM。10+ 工具调用的 ReAct 循环中，消息历史可增长到 5000+ token，`max_turns` 限制形同虚设。

### M13. 面板关闭时线程未终止
`panel.py:1-72` 无 `closeEvent` 覆盖。关闭面板时 ChatWorker 和 ExecutionEngine 线程继续运行，面板不可见后可能尝试发射信号导致崩溃。

### M14. _clear_conversation 不清除运行中的 worker/engine
`chat_widget.py:531-536` 不检查或取消 `self._worker` 或 `self._engine`。用户清空对话时如有 LLM 调用进行中，worker 继续运行，完成信号连接到正在销毁的 widget 可导致释放后使用。

### M15. 翻译条目原文可作为间接 LLM 注入向量
当 LLM 通过 `get_visible_entries` 等工具获取翻译条目时，条目的 `original` 字段可包含提示注入内容。游戏模组中的文本可被故意写入劫持指令。

### M16. 输入校验模式过于激进，可能误伤合法翻译内容
`input_validator.py:9-27` 的 `_INJECTION_PATTERNS` 包含 SQL/XSS/命令注入模式。翻译文本可能包含合法的 HTML 标签 (游戏标记语言)、SQL-like 关键词 (NPC 对话中的 "SELECT")，导致合法翻译操作被拒绝。

---

## Minor 级问题 (21 项)

| # | 分类 | 问题 | 文件:行 |
|---|------|------|---------|
| m1 | 架构 | `_filter_entries` 前缀 `_` 但与 `__all__` 公开导出矛盾 | `tools/base.py:210`, `__init__.py:50` |
| m2 | 死代码 | deprecated `get_collection_summary` 仍注册为活跃工具 | `tool_registry.py:118-124` |
| m3 | 死代码 | `chat_widget._on_skill` 方法无调用者 | `chat_widget.py:498` |
| m4 | 死代码 | `infra/markdown_renderer.py` 未见任何引用 | `infra/__init__.py:5` |
| m5 | 重复 | parser 工具注册使用独有的 3 元组格式 (vs 标准 5 元组) | `tool_parser.py:128-143` |
| m6 | 命名 | parser 工具 `display_name` 使用 `description[:20]` 粗暴截断 | `tool_parser.py:138` |
| m7 | 文档 | ToolRegistry 方法无 docstring | `tool_registry.py:25-70` |
| m8 | 文档 | parser 工具参数 schema 仅有 path，缺少说明 | `tool_parser.py:140` |
| m9 | 并发 | 忙等轮询阻塞线程池工作线程长达 300 秒 | `execution_engine.py:93-105, 235-240` |
| m10 | 并发 | TaskManager progress 字典在锁外被修改 | `task_manager.py:85-90` |
| m11 | 并发 | 每个 BFS 层级创建/销毁 ThreadPoolExecutor | `execution_engine.py:267` |
| m12 | 内存 | ObservabilityCollector._session_tokens 永不重置 | `collector.py:22-23` |
| m13 | 内存 | _uploaded_docs 在对话清除后从未释放 | `chat_widget.py:39, 531-536` |
| m14 | 内存 | VectorStore 软删除泄漏 FAISS 索引内存 | `vector_store.py:56-62` |
| m15 | 内存 | _active.tools_called 每对话无限增长 | `collector.py:51` |
| m16 | UI | _on_llm_chunk 是空操作 (pass)，无流式 UI | `chat_widget.py:264-265` |
| m17 | UI | MarkdownRenderer 每条消息创建 15-20 个 QWidget | `markdown_renderer.py:348-383` |
| m18 | 资源 | TaskManager 单例在会话间永不重置 | `task_manager.py:29-41` |
| m19 | 资源 | _on_retry 3 秒等待超时无错误处理 | `chat_widget.py:467-474` |
| m20 | 权限 | `clear_all_filters` 误标为 write 而非 read | `tool_editor.py:367` |
| m21 | 敏感信息 | `list_local_projects` 暴露项目绝对路径 | `tool_default.py:131` |

---

## 各维度评分

| 维度 | 评分 | 关键短板 |
|------|------|---------|
| **功能正确性** | 32/60 | 异步无通知 (B2) + 前置条件缺失 (C3) + 配置虚假属性 (C4) + Prompt 无工作流 (M4) |
| **安全性** | 25/60 | ReAct 绕过护栏 (B1) + Prompt 注入 (C6) + MCP 无认证 (C7) + v1 无路径校验 (C8) |
| **性能** | 35/60 | UI 线程阻塞 (B1联动) + UI 线程 IO (C9) + 无 Token 预算 (M11) + 线程泄漏 (M13/M14) |
| **代码质量** | 35/60 | 测试空白 (C2) + 架构违规 (C1) + 死代码 RetryHandler (M1) + v1/namespace 双轨 (M2) |
| **平均** | **32/60** | |

---

## Plan 合规性摘要

### llm-chat (智能助手侧边栏面板)
| Story | 状态 | 结论 |
|-------|------|------|
| Story-01 (面板框架) | ✅ | 验收通过 |
| Story-02 (核心后端) | ✅ | 验收通过 |
| Story-03 (循环控制与 UI 卡片) | ✅ | 验收通过 |
| Story-04 (工具系统) | ✅ | 验收通过 |
| Story-05 (体验优化) | ✅ | 验收通过 |
| Story-06/07 (代码分层) | ✅ | 验收通过 (但 C1 context_builder 违规) |
| Story-08 (UX 翻新) | 📝 | Story-08-1 (MarkdownRenderer) 已编码但未被引用 (m4) |

### agent-upgrade (Agent 框架升级)
| Phase | 状态 | 结论 |
|-------|------|------|
| Phase 1 (infra/Skill/文件/记忆/自纠错) | ✅ | 通过 (但 RetryHandler 为死代码 M1) |
| Phase 2 P0 (Agent协作/安全护栏) | ✅ | 通过 (但护栏被 ReAct 绕过 B1) |
| Phase 2 P1 (Graph/Checkpoint) | ✅ | 通过 (但 ExecutionEngine._paused 共享 M8) |
| Phase 2 P2 (MCP/可观测) | ✅ | 通过 (但 MCP 无认证 C7) |

### agent-tool-expansion (Agent 工具扩展)
| Story | 状态 | 结论 |
|-------|------|------|
| Story-01 (基础设施) | ✅ | 通过 |
| Story-02 (TaskManager) | ✅ | 通过 (但无完成通知 B2) |
| Story-04/06/07/08/10/12 | ✅ | 通过 |
| Story-09 (翻译配置) | ⚠ | get_translation_config 虚假属性 (C4) |
| Story-13 (Agent 集成) | ✅ | 通过 |
| Story-14 (集成测试) | ✅ | 通过 (但覆盖不足 C2) |

---

## 修复优先级建议

### 紧急 (Blocker — 必须立即修复)
1. **ReAct 改为调用 execute_with_guardrails** (B1) — `chat_widget.py:419`
2. **TaskManager 添加完成回调/信号** (B2) — 通知 LLM 异步任务结果
3. **ExecutionEngine 使用传入的 middlewares** (B3) — `execution_engine.py:41`

### 高优 (Critical — 本周修复)
4. 修复 `context_builder.py` 的 ADR-008 违规 (C1)
5. 扩展 `get_translation_config` 返回后处理/术语/ParaTranz 配置 (C4, C5)
6. `start_translation` 添加前置条件检查 (C3)
7. 为 v1 工具添加路径校验 (C8)
8. 用户上传文件内容不直接注入系统提示词 (C6)
9. 记忆持久化移出 UI 线程 (C9)
10. ToolResult 添加错误分类字段 (C10)

### 中优 (Major — 本月修复)
11. 实例化 RetryHandler 或移除死代码 (M1)
12. 合并 v1/namespace 重复工具，废弃 v1 同步版本 (M2)
13. 系统提示词补充翻译工作流和错误恢复指导 (M4, M5)
14. 添加 Token 预算和截断机制 (M11)
15. MemoryStore 添加大小限制和淘汰策略 (M9)
16. 面板关闭时清理线程 (M13, M14)

---

## 签名

**QA 审查结论**: ⚠ **需修复 — 3 Blocker + 10 Critical + 16 Major**

在 Blocker 和 Critical 级问题修复前，Smart Assistant 存在严重的安全和功能缺陷，不建议在生产环境中使用自动模式。`/bm-plan` 应基于本报告制定修复计划。

**审查维度**: 功能测试 / 安全审查 / 性能审查 / 代码质量审查 (4 Agent 并行)
**审查日期**: 2026-05-11