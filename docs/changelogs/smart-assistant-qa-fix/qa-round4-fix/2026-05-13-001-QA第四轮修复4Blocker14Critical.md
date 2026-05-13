# 001: QA 第四轮修复 — 4 Blocker + 14 Critical

**日期**: 2026-05-13
**类型**: 改
**关联**: Epic: Smart Assistant QA 修复 > QA Round 4 修复批次

## 修改文件

### `src/transbridge/ui/tools/smart_assistant/chat_widget.py` (改)
- **修改内容**: 6 处修复 — (1) BR1: `ContextBuilder.build(self._ctx)` 改为 `ContextBuilder(self._ctx).build()`，修复静态调用导致的 AttributeError 崩溃；(2) BR2: `__init__` 添加 `self._pending_memory_context = ""`，修复首次对话 AttributeError；(3) CR3: `_on_tool_executed` 添加 PermissionGuard 预检查，admin/write 工具改用 QMessageBox 确认而非直接拒绝；(4) CR7: `_get_llm_client` 改为实例方法并缓存 client，仅配置变更时重建；(5) CR8: `_on_llm_finished/_on_llm_error` 末尾添加信号断开 + deleteLater + None 清理；(6) CR15: `_on_plan_confirmed` 移除 daemon Thread，改用 `engine._executor.submit(engine.execute, steps)`
- **原因**: BR1/BR2 导致面板对话完全不可用（Blocker）。CR3/CR7/CR8/CR15 修复 ReAct 权限确认体验、HTTP 连接池浪费、QThread 泄漏、冗余线程创建等性能和安全问题。

### `src/transbridge/ui/tools/smart_assistant/panel.py` (改)
- **修改内容**: CR9: `closeEvent` 中添加 ObservabilityCollector.token_stats_updated 和 TaskManager.task_completed/task_failed 信号断开（try/except 包裹），在 worker/engine 清理之前执行
- **原因**: 面板关闭时信号连接未断开导致相关对象无法 GC，线程和 QObject 泄漏跨会话累积

### `src/transbridge/smart_assistant/execution_engine.py` (改)
- **修改内容**: 3 处修复 — (1) CR1: RetryHandler 实例化改为 `RetryHandler(llm_client=create_llm_client(cfg))`，从 LLMConfig 加载配置并创建 llm_client 传入；(2) CR6: 新增 `shutdown()` 方法调用 `self._executor.shutdown(wait=False)`；(3) CR11: `execute_graph` 中 checkpoint 保存 `except Exception: pass` 改为 `except Exception: logger.warning(...)`
- **原因**: CR1 修复 Reflexion 自纠错机制（llm_client 缺失导致 analyze_and_adjust 直接返回 None）。CR6 修复 ThreadPoolExecutor 泄漏（Engine 无 shutdown 方法）。CR11 修复长时间任务 checkpoint 静默失败。

### `src/transbridge/smart_assistant/mcp/server.py` (改)
- **修改内容**: CR4: `run_stdio()` 中添加 `logger.warning()`，当 `auth_token` 为空时告警"MCP Server 未配置 auth_token，任何本地进程均可调用工具"
- **原因**: MCP 默认无认证机制，需要至少警告用户当前不安全状态

### `src/transbridge/config/llm.py` (改)
- **修改内容**: CR5: `save_to_file()` 方法上方添加 `# WARNING:` 注释，说明 API Key 以明文存储在 INI 文件中，生产环境应使用系统密钥链
- **原因**: API Key 明文存储是安全风险，但加密需额外依赖，先标记为已知限制

### `src/transbridge/smart_assistant/agents/orchestrator.py` (改)
- **修改内容**: CR2: `map_to_steps()` 方法中添加注释块，说明 LLM prompt 的 `action` 字段是 human-readable 描述而非有效 tool_name，标记 TODO 需要在 prompt schema 中添加 `tool_name` 字段
- **原因**: Orchestrator 将人类描述误用作工具名导致编排模式不可用，完整修复需要在 LLM prompt 层面重新设计

### `src/transbridge/smart_assistant/tools/tool_paratranz.py` (改)
- **修改内容**: CR10: `_tool_upload_entries` 中逐条目上传的 `except Exception: pass` 改为收集失败条目列表 `failed_items`，包含 `key` 和 `error` 字段，在 ToolResult 中返回并在 message 中显示失败数量
- **原因**: 静默吞异常导致用户无法得知哪些条目上传失败，收到误导性成功消息

### `src/transbridge/smart_assistant/file_parser/paratranz_parser.py` (改)
- **修改内容**: CR12: `_parse_zip` 方法中保存 `zf.namelist()` 到变量，复用于 metadata 构建，删除第二次 `zipfile.ZipFile(path, "r")` 打开
- **原因**: 同一 ZIP 文件被重复打开两次，浪费文件句柄和 I/O

### `src/transbridge/smart_assistant/observability/collector.py` (改)
- **修改内容**: CR13: `_cleanup_old` 中的 `except Exception: pass` 改为 `except OSError: pass  # 清理旧追踪文件失败不影响主流程`
- **原因**: 宽泛的 `except Exception` 吞掉所有异常，`OSError` 更精确表达清理操作的预期异常类型

### `src/transbridge/smart_assistant/graph_executor.py` (改)
- **修改内容**: BR3: 文件顶部添加注释标记 `GraphExecutor` ABC 为零引用死代码，保留以备后续使用
- **原因**: ADR-011 规划的 StatefulDAGExecutor(GraphExecutor) 继承从未实现，ABC 孤立

### `src/transbridge/smart_assistant/__init__.py` (改)
- **修改内容**: BR3: 移除 `from .graph_executor import GraphExecutor` 导入和 `__all__` 中的 `"GraphExecutor"` 导出，添加注释说明原因
- **原因**: 死代码不应导出，避免误导调用方以为这是正式接口契约

### `src/transbridge/smart_assistant/skills/skill_executor.py` (改)
- **修改内容**: BR4: 模块 docstring 中记录已知反向依赖（SkillExecutor 依赖 UI 层 ChatWidget）；`__init__` 参数添加 `chat_widget: "ChatWidget"` 前向引用类型标注；在 `add_system_message`/`set_input`/`_on_send` 调用处添加 `# BR4:` 注释标记
- **原因**: 违反 ADR-008 UI/backend 分层，需要文档化并标记为待重构
