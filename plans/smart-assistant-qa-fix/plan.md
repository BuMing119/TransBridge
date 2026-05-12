# Smart Assistant QA 全面修复

**对应需求**: FR7.15 — Smart Assistant QA 审查修复
**技术模块**: `smart_assistant/`、`ui/tools/smart_assistant/`、`infra/`
**业务域**: AI 辅助翻译 — 安全/质量/性能修复
**状态**: 已确认
**创建日期**: 2026-05-12
**输入文档**: `docs/test-reports/smart-assistant.md`（2026-05-11 QA全面审查报告）

## 功能边界

### 范围内

- 修复测试报告中的 **3 Blocker + 10 Critical + 16 Major + 21 Minor** = 50 项问题
- 安全护栏（B1/B3/C6/C7/C8/M15/M16）：护栏绕过、Prompt注入、MCP认证、路径校验
- 异步通知（B2）：TaskManager 添加 Qt 信号回调
- 配置完整性（C3/C4/C5/C10）：前置条件检查、配置暴露、错误分类
- 线程与资源（C9/M7/M8/M9/M10/M11/M12/M13/M14）：线程泄漏、内存管理、Token预算
- 代码清理（C1/M1/M2/M3/M6）：ADR-008违规、死代码、去重、装饰器统一
- 小修复（m1-m21）：命名、文档、权限、UI 渲染
- 测试补充（C2）：覆盖核心模块

### 范围外

- 新功能开发
- UI/UX 新增需求（仅修复不重写）
- 架构重构（仅修复违规项，不改架构方向）
- 第三方依赖引入

---

## Story 清单

### Story-01: Blocker 安全护栏修复

**Phase**: 1 | **预估**: 2h | **优先级**: Blocker
**覆盖问题**: B1, B3
**详细文档**: `plans/smart-assistant-qa-fix/stories/story-01-blocker-guardrail-fix.md`

**验收标准**:
- [ ] ReAct 模式下工具调用走 `execute_with_guardrails()`，PermissionGuard/InputValidationGuard/OutputValidationGuard 全部生效
- [ ] `ExecutionEngine.__init__` 正确使用传入的 `middlewares` 参数构建 `_guards` 列表
- [ ] 用户可在配置中禁用某类中间件且实际生效
- [ ] admin 级工具（write_to_esp/eet/xt）在自动模式下仍需用户确认
- [ ] 路径遍历检测、扩展名白名单、输出脱敏在 ReAct 路径下正常拦截

**实现步骤**:
1. `chat_widget.py:419` — `_on_tool_executed` 中将 `spec.execute(step.get("args", {}), self._ctx)` 替换为 `execute_with_guardrails(spec, step.get("args", {}), self._ctx)` → `chat_widget.py`
2. `execution_engine.py:41` — `_build_guard_chain()` 改为使用 `self._middlewares` 列表（从 `__init__` 的 `middlewares` 参数构建），而非硬编码的三件套 → `execution_engine.py`
3. 验证：启动应用 → 自动模式 → 确认工具调用触发权限弹窗 → 人工验证

---

### Story-02: 异步任务完成通知

**Phase**: 2 | **预估**: 2h | **优先级**: Blocker
**覆盖问题**: B2
**详细文档**: `plans/smart-assistant-qa-fix/stories/story-02-async-task-notification.md`

**验收标准**:
- [ ] `TaskManager` 新增 `task_completed(task_id, result)` 和 `task_failed(task_id, error)` 两个 pyqtSignal
- [ ] `start_translation` / `start_polish` 后台线程完成时发射对应信号
- [ ] `ChatWidget` 连接信号，收到完成通知后将结果以 observation 消息追加到对话
- [ ] LLM 可通过 `get_task_status` 查询进度，也可通过系统通知得知完成
- [ ] 系统提示词告知 LLM 异步任务完成后会自动通知

**实现步骤**:
1. `TaskManager` 继承 `QObject`，添加 `task_completed` / `task_failed` pyqtSignal，线程完成时 `emit` → `task_manager.py`
2. `ChatWidget` 连接 `TaskManager` 信号，发射时调用 `add_observation()` 追加结果 → `chat_widget.py`
3. 更新系统提示词，告知 LLM "异步任务完成后会自动通知结果" → `prompts.py`

---

### Story-03: 安全加固

**Phase**: 3 | **预估**: 3h | **优先级**: Critical
**覆盖问题**: C6, C7, C8, M15, M16
**详细文档**: `plans/smart-assistant-qa-fix/stories/story-03-security-hardening.md`

**验收标准**:
- [ ] 用户上传文件内容不再直接拼接到系统提示词，改为存储为内存条目后注入 `{uploaded_doc_summary}` 占位符（仅文件名+字符数摘要）
- [ ] MCP stdio 通道支持可选的 token 认证（INI 配置 `[mcp] auth_token`，空则不启用）
- [ ] `tool_v1.py` 的 `_tool_write_back` 和 `_tool_export_json` 添加 `_validate_path` / `_validate_output_path` 检查（与 namespace 工具一致）
- [ ] 输入校验正则 `_INJECTION_PATTERNS` 放宽：允许合法 HTML 标签（`<font>`, `<b>`, `<i>`, `<br>`）和 SQL-like 关键词（`SELECT`, `FROM`, `WHERE` 在翻译文本中）
- [ ] 翻译条目原文作为间接注入向量的风险记录在已知限制中（不修复，通过输出护栏兜底）

**实现步骤**:
1. `context_builder.py:46-48` — 上传文件内容改为存储到 MemoryStore，系统提示词中仅注入文件名+字符数摘要 → `context_builder.py` + `prompts.py`
2. `mcp/server.py:22` — 添加 token 认证：从 `[mcp] auth_token` 读取，验证 `Authorization` 头 → `mcp/server.py`
3. `tool_v1.py:101-139` — 在 `_tool_export_json` 和 `_tool_write_back` 前添加路径校验 → `tool_v1.py`
4. `guardrails/input_validator.py:9-27` — `_INJECTION_PATTERNS` 中添加 HTML 标签白名单，移除 SQL 关键词误伤 → `input_validator.py`
5. 更新 `[mcp]` INI 段添加 `auth_token` 配置项 → `config/` + `chat_widget.py`

---

### Story-04: 配置与工具前置条件

**Phase**: 4 | **预估**: 3h | **优先级**: Critical
**覆盖问题**: C3, C4, C5, C10, M4, M5
**详细文档**: `plans/smart-assistant-qa-fix/stories/story-04-config-precondition.md`

**验收标准**:
- [ ] `start_translation` 执行前检查：API Key 已配置、collection 非空、术语来源已设
- [ ] `get_translation_config` 返回真实的后处理配置（`pp_*` 前缀字段）和术语数据库信息
- [ ] `get_app_state` 返回 ParaTranz API 配置状态（token 是否已配置、URL）
- [ ] `ToolResult.fail()` 支持 `error_category`（network/auth/input/permission/internal）、`error_code`、`recovery_action` 字段
- [ ] 系统提示词包含正确的翻译工作流指导（确认配置→检查术语→设作用域→预览→翻译→轮询→检查→后处理→写回）
- [ ] 系统提示词包含错误恢复策略（网络故障可重试、权限拒绝不可重试等）

**实现步骤**:
1. `tool_translator.py:19-32` — `_tool_start_translation` 开头添加 API Key / 术语 / 后处理开关检查 → `tool_translator.py`
2. `tool_translator.py:226-227` — 修复 `get_translation_config`：从 `LLMConfig` 读取 `pp_*` 字段聚合为后处理配置，读取术语数据库文件信息 → `tool_translator.py`
3. `tool_default.py:15-36` — `_tool_get_app_state` 扩展返回 ParaTranz 配置状态 → `tool_default.py`
4. `tools/base.py:85-86` — `ToolResult.fail()` 添加 `error_category`/`error_code`/`recovery_action` 可选参数 → `base.py`
5. `prompts.py` — 补充翻译工作流 + 错误恢复策略两段 Prompt → `prompts.py`

---

### Story-05: 线程与资源生命周期管理

**Phase**: 5 | **预估**: 4h | **优先级**: Critical/Major
**覆盖问题**: C9, M7, M8, M9, M10, M11, M12, M13, M14
**详细文档**: `plans/smart-assistant-qa-fix/stories/story-05-thread-resource.md`

**验收标准**:
- [ ] 记忆持久化从 UI 线程移出：`MemoryStore.add()` 提交到后台队列，由专用 `QThread` 异步写入
- [ ] `AgentWorker.cancel()` 可中断正在执行的工具调用（通过 `_cancelled` 标志在工具执行循环中检查）
- [ ] `ExecutionEngine._paused` 改为实例级属性（非类级），不同会话独立暂停
- [ ] `MemoryStore` 添加 `max_entries`（默认 1000）+ LRU 淘汰策略
- [ ] `ConversationManager._trim()` 裁剪时同步移除 observation/plan_result 消息
- [ ] `build_tool_schema_for_prompt()` 按当前 Agent namespace 过滤工具，仅发送相关工具 schema（token 节省 50%+）
- [ ] `add_observation()` 结果文本超过 2000 字符时自动截断
- [ ] `panel.py` 添加 `closeEvent` 覆盖：关闭面板时 cancel worker + stop engine
- [ ] `_clear_conversation()` 检查并取消运行中的 worker/engine

**实现步骤**:
1. `memory_store.py:54-64` — 添加 `_write_queue` + `MemoryWriterThread(QThread)`，`add()` 只入队立即返回，后台线程批量写入 → `memory_store.py`
2. `agent_worker.py:20-53` — `run()` 中在工具执行前后检查 `_cancelled` 标志 → `agent_worker.py`
3. `execution_engine.py:179` — `_paused` 从类属性改为 `__init__` 中的实例属性 → `execution_engine.py`
4. `memory_store.py` — 添加 `max_entries` 限制 + `_evict_lru()` → `memory_store.py`
5. `conversation_manager.py:40-54` — `_trim()` 扩展为裁剪所有角色消息（含 tool/observation/system） → `conversation_manager.py`
6. `tool_registry.py:59-70` — `build_tool_schema_for_prompt(namespace)` 按 namespace 过滤 → `tool_registry.py`
7. `chat_widget.py` — `add_observation()` 添加 2000 字符截断 + observation 消息 `_trim()` 兼容 → `chat_widget.py`
8. `panel.py` — 添加 `closeEvent` 覆盖，清理 worker + engine → `panel.py`
9. `chat_widget.py:531-536` — `_clear_conversation` 添加 worker/engine 取消逻辑 → `chat_widget.py`

---

### Story-06: 代码清理与架构修复

**Phase**: 6 | **预估**: 4h | **优先级**: Major/Minor
**覆盖问题**: C1, M1, M2, M3, M6, m1-m21
**详细文档**: `plans/smart-assistant-qa-fix/stories/story-06-code-cleanup.md`

**验收标准**:
- [ ] `context_builder.py` 不再 `from src.transbridge.ui.context import AppContext`，改用依赖注入
- [ ] `RetryHandler` 在 `ExecutionEngine.__init__` 中实例化，或删除 `reflexion/retry_handler.py` 死代码
- [ ] `collection-is-None` 检查 6 处统一改用 `@require_collection` 装饰器
- [ ] v1 同步工具标记为 `deprecated`，namespace 异步工具为推荐替代
- [ ] ReAct 模式的 `_handle_tool_result` 接入 `RetryHandler`
- [ ] 21 项 Minor 问题全部修复

**实现步骤**:
1. `context_builder.py:4` — 移除 `from src.transbridge.ui.context import AppContext`，`AppContext` 实例由调用方通过构造函数传入 → `context_builder.py` + `chat_widget.py`
2. `execution_engine.py:39` — 实例化 `RetryHandler`（或若确定废弃则删除 `reflexion/retry_handler.py` 及其 import） → `execution_engine.py`
3. 6 个工具文件中重复的 `if self._ctx.collection is None` 检查统一改为 `@require_collection` 装饰器 → `tool_translator.py`, `tool_v1.py`, `tool_writer.py`, `tool_paratranz.py`, `tool_proofreader.py`
4. `tool_v1.py` — 每个同步工具 docstring 添加 `@deprecated` 标记，指向对应 namespace 工具 → `tool_v1.py`
5. `chat_widget.py:444-458` — `_handle_tool_result` 包裹重试逻辑（复用 RetryHandler） → `chat_widget.py`
6. 批量修复 Minor 问题：命名 (m1/m6)、死代码 (m2/m3/m4)、注册格式 (m5)、文档 (m7/m8)、并发 (m9/m10/m11)、内存 (m12/m13/m14/m15)、UI (m16/m17)、资源 (m18/m19)、权限 (m20)、敏感信息 (m21) → 各涉及文件

**Minor 修复清单**:
| # | 文件 | 修复 |
|---|------|------|
| m1 | `tools/base.py:210`, `__init__.py:50` | 移除 `_filter_entries` 从 `__all__` 导出或去掉下划线 |
| m2 | `tool_registry.py:118-124` | 移除 deprecated `get_collection_summary` 注册 |
| m3 | `chat_widget.py:498` | 删除无调用者的 `_on_skill` 方法 |
| m4 | `infra/__init__.py:5` | 确认 `markdown_renderer.py` 被引用或移除导出 |
| m5 | `tool_parser.py:128-143` | 统一为 5 元组注册格式 |
| m6 | `tool_parser.py:138` | `display_name` 不使用 `description[:20]` 截断 |
| m7 | `tool_registry.py:25-70` | 添加方法 docstring |
| m8 | `tool_parser.py:140` | 参数 schema 补充说明 |
| m9 | `execution_engine.py:93-105, 235-240` | 忙等轮询改为 `Condition.wait(timeout)` |
| m10 | `task_manager.py:85-90` | progress 字典修改移到锁内 |
| m11 | `execution_engine.py:267` | 复用 ThreadPoolExecutor 而非每层级创建/销毁 |
| m12 | `collector.py:22-23` | 会话切换时重置 `_session_tokens` |
| m13 | `chat_widget.py:39, 531-536` | `_clear_conversation` 时释放 `_uploaded_docs` |
| m14 | `vector_store.py:56-62` | 软删除时清理 FAISS 索引内存 |
| m15 | `collector.py:51` | 对话清除时重置 `_active.tools_called` |
| m16 | `chat_widget.py:264-265` | 删除空的 `_on_llm_chunk`（S08-4 已实现流式） |
| m17 | `markdown_renderer.py:348-383` | 复用组件而非每消息创建 15-20 QWidget |
| m18 | `task_manager.py:29-41` | TaskManager 单例在会话结束时可重置 |
| m19 | `chat_widget.py:467-474` | `_on_retry` 3s 超时添加错误处理 |
| m20 | `tool_editor.py:367` | `clear_all_filters` 权限改为 `read` |
| m21 | `tool_default.py:131` | `list_local_projects` 仅返回项目名，不暴露绝对路径 |

---

### Story-07: 测试补充

**Phase**: 7 | **预估**: 4h | **优先级**: Critical
**覆盖问题**: C2
**详细文档**: `plans/smart-assistant-qa-fix/stories/story-07-testing.md`

**验收标准**:
- [ ] `ChatWorker` 测试：流式响应 / cancel / 错误处理 / token usage 统计
- [ ] `ConversationManager` 测试：max_turns 裁剪（含 observation 消息）/ 上下文长度限制
- [ ] `ExecutionEngine.execute_graph()` 测试：DAG 拓扑排序 / 层级并行 / checkpoint 暂停恢复 / 重试
- [ ] `RetryHandler` 测试：可重试错误 vs 不可重试错误 / 参数调整 / MAX_RETRIES
- [ ] `MemoryStore` / `MemoryRetriever` 测试：添加 / 语义搜索 / 精确搜索 / LRU 淘汰
- [ ] `ObservabilityCollector` 测试：token 统计 / 追踪持久化 / 过期清理
- [ ] `MarkdownRenderer` 测试：12 种格式渲染 / 容错降级 / 链接点击
- [ ] `ContextBuilder` 测试：系统提示词构建 / 上传文件摘要注入 / 工具 schema 注入
- [ ] MCP 模块测试：tools/list / tools/call / auth 拒绝 / 错误处理
- [ ] 5 个知识缺口验证测试（见 `docs/smart-assistant-knowledge-gaps.md`）

**实现步骤**:
1. 创建 `tests/test_chat_worker.py`：mock LLMClient，验证流式信号发射、cancel 中断、异常处理 → `tests/test_chat_worker.py` (新建)
2. 创建 `tests/test_conversation_manager.py`：填满 max_turns+observation，验证 trim 正确性 → `tests/test_conversation_manager.py` (新建)
3. 创建 `tests/test_execution_engine.py`：构造 DAG → execute_graph → 验证拓扑排序+层级并行+checkpoint → `tests/test_execution_engine.py` (新建)
4. 创建 `tests/test_retry_handler.py`：各种错误类型 → 验证 should_retry 判断 → `tests/test_retry_handler.py` (新建)
5. 创建 `tests/test_memory.py`：MemoryStore + MemoryRetriever + LRU 淘汰 → `tests/test_memory.py` (新建)
6. 创建 `tests/test_observability.py`：token 统计 + 追踪持久化 + 过期清理 → `tests/test_observability.py` (新建)
7. 创建 `tests/test_markdown_renderer.py`：12 格式渲染 + 容错 → `tests/test_markdown_renderer.py` (新建)
8. 创建 `tests/test_context_builder.py`：系统提示词构建 + 注入 → `tests/test_context_builder.py` (新建)
9. 扩展 `tests/test_agent_tool_integration.py`：添加 MCP 协议测试 + 知识缺口验证测试 → `tests/test_agent_tool_integration.py`

---

## 架构依赖

- **ADR-008** (代码分层)：Story-06 C1 修复 context_builder 违反 UI→backend 单向依赖
- **ADR-009** (Agent/记忆/自纠错)：Story-01 B1 修复 RetryHandler 集成，Story-05 M9 添加 MemoryStore 淘汰策略
- **ADR-012** (安全护栏/MCP/遥测)：Story-01 确保护栏对所有路径生效，Story-03 C7 MCP 认证，Story-05 M13/M14 生命周期管理
- **ADR-011** (Graph 编排引擎)：Story-05 M8 修复 `_paused` 实例共享

## 风险与回退方案

- **风险**: Story-01 将 ReAct 接入 guardrails 后，PermissionGuard 弹窗过于频繁影响体验 → **缓解**: 同一会话内相同工具的确认可缓存（allow_always 选项已在 ADR-012 中定义）
- **风险**: Story-05 MemoryStore 改为异步写入后，应用崩溃可能丢失未刷盘的记忆 → **缓解**: 面板关闭时 flush 队列；记忆丢失非关键路径
- **风险**: Story-06 统一 `@require_collection` 装饰器可能遗漏边界情况 → **缓解**: 逐文件替换，每个文件替换后运行现有测试验证
- **风险**: Story-07 新增测试用例数量大，可能发现更多隐藏问题 → **缓解**: 若发现新问题，记录到 changelog 并在后续 Story 中修复（不阻塞当前修复）
