# 001: Story-12 MCP Server 编码实现

**日期**: 2026-05-10
**类型**: 增/改
**关联**: Epic: Agent框架升级 > Story 12: MCP Server

## 修改文件

### `src/transbridge/smart_assistant/mcp/adapter.py` (增)
- **修改内容**: MCPAdapter 类——list_tools() 返回 MCP Tool JSON Schema（name/description/inputSchema），call_tool() 执行并返回 MCP CallToolResult 格式。_is_exposed() 安全过滤：admin 需白名单/write 默认 deny（MCP 通道无 UI 确认机制）。_build_json_schema() 从 ToolSpec.parameters 构造 JSON Schema（type/description/required）。
- **原因**: FR7.13.5 MCP Server 的 ToolSpec→MCP 协议映射层。安全约束确保高风险工具不会通过 MCP 通道意外暴露。

### `src/transbridge/smart_assistant/mcp/server.py` (增)
- **修改内容**: MCPServer 类——run_stdio() 从 stdin 逐行读取 JSON-RPC 2.0 请求，路由 tools.list/tools.call 方法。错误码：-32700 Parse error / -32601 Method not found。stop() 优雅退出。_jsonrpc_result() 包装标准 JSON-RPC 响应。
- **原因**: FR7.13.5 MCP stdio JSON-RPC Server 实现。默认 disabled，用户通过 `[mcp]` INI section 显式启用。

### `src/transbridge/smart_assistant/mcp/__init__.py` (增)
- **修改内容**: 导出 MCPServer/MCPAdapter 2 个公开符号。
- **原因**: mcp/ 子包公开 API。

### `src/transbridge/config/llm.py` (改)
- **修改内容**: LLMConfig 新增 [mcp] INI section——mcp_enabled(bool=False)/mcp_transport(str="stdio")/mcp_admin_tool_whitelist(str="")/mcp_write_tool_policy(str="deny")。save_to_file()/load_from_file() 追加对应读写逻辑（has_section 判空后读取）。
- **原因**: S12 步骤4 MCP 配置持久化。用户通过 INI 文件控制 MCP Server 的启用/禁用和安全策略。

### `src/transbridge/ui/app.py` (改)
- **修改内容**: main() 函数中追加 MCPServer 条件启动逻辑——从 LLMConfig 读取 mcp_enabled，enabled=true 时创建 MCPAdapter + MCPServer，在 daemon 线程中调用 run_stdio()。
- **原因**: S12 步骤5 启动入口集成。MCP Server 在 daemon 线程运行，不阻塞应用主线程。

### `src/transbridge/smart_assistant/__init__.py` (改 — QA 修复)
- **修改内容**: 新增导入 mcp/子包（MCPServer/MCPAdapter）+ guardrails/子包（GuardMiddleware/GuardResult/PermissionGuard/InputValidationGuard/OutputValidationGuard）。__all__ 从 16 个符号扩展到 27 个。
- **原因**: QA 发现 __init__.py 未导出 guardrails/observability/mcp 子包符号（Minor m3），在 Phase 2 QA 修复轮次中补全。
