# 001: ReAct 路径安全护栏接入 + ExecutionEngine middlewares 修复

**日期**: 2026-05-12
**类型**: 改
**关联**: Epic: Smart Assistant QA 全面修复 > Story 01: Blocker 安全护栏修复

## 修改文件

### `src/transbridge/smart_assistant/tools/base.py` (改)
- **修改内容**: `execute_with_guardrails()` 新增 `middlewares: list | None = None` 参数。当 middlewares 为 None 时 fallback 到 `_build_guard_chain()` 默认链；传入 [] 则跳过所有护栏。Before/After 链改为迭代 guards 列表而非硬编码解构三个 guard
- **原因**: B1 修复 — ReAct 路径需传入用户配置的护栏链；B3 修复 — ExecutionEngine 需使用传入的 middlewares 而非忽略

### `src/transbridge/ui/tools/smart_assistant/chat_widget.py` (改)
- **修改内容**: 新增 `_middlewares` 属性和 `_ensure_middlewares()` 方法（延迟构建护栏链，读取 LLMConfig 按需组装 PermissionGuard/InputValidationGuard/OutputValidationGuard）。`_on_tool_executed()` 从 `spec.execute(args, self._ctx)` 改为调用 `execute_with_guardrails(spec, args, exec_ctx, middlewares=self._ensure_middlewares())`，构造 ExecutionContext 包装 AppContext + TaskManager。`_handle_tool_result()` 适配 ToolResult 类型（原仅接受 dict）。`_on_plan_confirmed()` 改为复用 `_ensure_middlewares()`
- **原因**: B1 修复 — 原 ReAct 路径直接调用 `spec.execute()` 完全绕过 PermissionGuard/InputValidationGuard/OutputValidationGuard，LLM 可自由调用 admin 级工具无需确认

### `src/transbridge/smart_assistant/execution_engine.py` (改)
- **修改内容**: `__init__` 中护栏链构建从 `_build_guard_chain()` 改为优先使用传入的 `middlewares` 参数（`if middlewares: self._guards = list(middlewares)`），无传参时 fallback 到默认链
- **原因**: B3 修复 — 原代码完全忽略 `middlewares` 参数（该参数被传给 `super().__init__(parent)` 即 QObject），用户即使配置中禁用了某类中间件也仍然生效
