# 001: ADR-008 违规修复 + RetryHandler 实例化 + @require_collection

**日期**: 2026-05-12
**类型**: 改
**关联**: Epic: Smart Assistant QA 全面修复 > Story 06: 代码清理与架构修复

## 修改文件

### `src/transbridge/smart_assistant/context_builder.py` (改)
- **修改内容**: 移除顶层 `from src.transbridge.ui.context import AppContext`。`ContextBuilder` 从纯静态类改为支持依赖注入：新增 `__init__(self, ctx=None)` 构造函数，`build(ctx=None)` 方法优先使用参数 ctx 其次 self._ctx。原 `build(ctx: AppContext)` 类型注解移除
- **原因**: C1 — 原代码从 `ui/` 导入 AppContext 违反 ADR-008「backend 不依赖 UI」原则，Backend 不应直接 import UI 模块

### `src/transbridge/smart_assistant/execution_engine.py` (改)
- **修改内容**: `__init__` 中 `self._retry_handler = None` 改为 try-import `RetryHandler` 并实例化：`from src.transbridge.smart_assistant.reflexion.retry_handler import RetryHandler; self._retry_handler = RetryHandler()`，ImportError 时降级为 None
- **原因**: M1 — 原 `self._retry_handler = None` 导致 `_run_single` 中的重试循环因 `self._retry_handler is None` 短路，永远走"立即放弃"分支，`reflexion/retry_handler.py` 的 52 行代码处于死代码状态

### `src/transbridge/smart_assistant/tools/base.py` (改)
- **修改内容**: 新增 `require_collection(func)` 装饰器函数（functools.wraps 包装）：检查 `ctx.collection` 非空，否则返回 `ToolResult.fail("当前没有加载翻译集合", error_category="input", error_code="COLLECTION_NOT_LOADED")`
- **原因**: M3 — 6 个工具文件（tool_translator/tool_v1/tool_writer/tool_paratranz/tool_proofreader）各自手工实现 collection-is-None 检查，而 tool_editor 已使用装饰器模式，不一致增加维护负担
