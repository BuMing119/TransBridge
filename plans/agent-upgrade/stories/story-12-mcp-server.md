# Story 12: MCP Server

**所属方案**: `plans/agent-upgrade/plan.md`
**技术模块**: smart_assistant/mcp
**状态**: 已确认
**创建日期**: 2026-05-10

## 前置依赖

### 上游 Story
- Story-06（同 plan）：必须已完成 → ToolRegistry namespace 扩展就绪
- Story-08（同 plan）：必须已完成 → ToolSpec.permission 字段就绪（MCP 安全约束依赖权限分级）

### 引用的架构决策
- ADR-012: MCP Server（stdio JSON-RPC + ToolSpec 映射 + 安全约束）

## 验收标准

- [ ] `MCPServer` 类：`run_stdio()` 从 stdin 读取 JSON-RPC 请求，处理后写入 stdout
- [ ] `MCPAdapter` 类：ToolSpec → MCP Tool 定义 JSON Schema 转换
- [ ] `tools/list` 方法：返回 ToolRegistry 中所有非 admin 工具的列表
- [ ] `tools/call` 方法：接收 tool_name + arguments → ToolSpec.execute() → 返回 MCP 格式
- [ ] admin 级工具默认不暴露；通过 `[mcp]` INI 白名单控制
- [ ] write 级工具 MCP 通道中可配置策略（allow/deny，默认 deny）
- [ ] `[mcp]` INI section：enabled/transport/admin_tool_whitelist/write_tool_policy
- [ ] MCP Server 启用时在应用启动日志中输出监听信息

## 数据流

```
外部 MCP Client (子进程)
  │
  │ stdio: stdin → JSON-RPC request
  ▼
MCPServer.run_stdio()
  │
  ├─→ 解析 JSON-RPC: {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
  │
  ├─→ 路由:
  │     "tools/list" → MCPAdapter.list_tools()
  │       ├─ ToolRegistry.list_all()
  │       ├─ 过滤: admin 工具不在白名单 → 跳过
  │       ├─ write 工具 + write_tool_policy=deny → 跳过
  │       └─ 转换为 MCP Tool 格式 → JSON Schema
  │
  │     "tools/call" → MCPAdapter.call_tool(name, arguments)
  │       ├─ ToolRegistry.get(name, namespace=None)
  │       ├─ 权限检查（同 tools/list 过滤规则）
  │       ├─ spec.execute(arguments, ctx)
  │       └─ 包装为 MCP CallToolResult 格式
  │
  └─→ stdout: JSON-RPC response
```

## 关键接口

### server.py（新建）

```python
import sys
import json

class MCPServer:
    """MCP stdio JSON-RPC Server。在独立线程中运行，不阻塞主线程。"""

    def __init__(self, registry: ToolRegistry, adapter: MCPAdapter, ctx):
        self._registry = registry
        self._adapter = adapter
        self._ctx = ctx
        self._running = False

    def run_stdio(self):
        """从 stdin 逐行读取 JSON-RPC 请求，处理后写入 stdout。"""
        self._running = True
        for line in sys.stdin:
            if not self._running:
                break
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                response = self._handle_request(request)
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
            except json.JSONDecodeError:
                sys.stdout.write(json.dumps({
                    "jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "Parse error"}
                }) + "\n")
                sys.stdout.flush()

    def _handle_request(self, request: dict) -> dict:
        method = request.get("method", "")
        req_id = request.get("id")
        if method == "tools/list":
            return self._list_tools(req_id)
        elif method == "tools/call":
            return self._call_tool(request.get("params", {}), req_id)
        else:
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}}

    def stop(self):
        self._running = False
```

### adapter.py（新建）

```python
class MCPAdapter:
    """ToolSpec → MCP 协议映射适配器。"""

    def __init__(self, registry, config: dict):
        self._registry = registry
        self._admin_whitelist: list[str] = config.get("admin_tool_whitelist", [])
        self._write_policy: str = config.get("write_tool_policy", "deny")

    def list_tools(self) -> list[dict]:
        """返回 MCP 格式的工具列表。过滤 admin/write 受限工具。"""
        tools = []
        for spec in self._registry.list_all():
            if not self._is_exposed(spec):
                continue
            tools.append({
                "name": spec.name,
                "description": spec.description,
                "inputSchema": self._build_json_schema(spec.parameters),
            })
        return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        """执行工具并返回 MCP CallToolResult 格式。"""
        spec = self._registry.get(name, namespace=None)
        if spec is None:
            return {"content": [{"type": "text", "text": f"工具不存在: {name}"}], "isError": True}
        if not self._is_exposed(spec):
            return {"content": [{"type": "text", "text": f"工具未暴露: {name}"}], "isError": True}
        try:
            result = spec.execute(arguments, self._ctx)
            return {"content": [{"type": "text", "text": result.get("message", "")}]}
        except Exception as exc:
            return {"content": [{"type": "text", "text": f"执行异常: {exc}"}], "isError": True}

    def _is_exposed(self, spec) -> bool:
        perm = getattr(spec, 'permission', 'read')
        if perm == "admin" and spec.name not in self._admin_whitelist:
            return False
        if perm == "write" and self._write_policy == "deny":
            return False
        return True

    def _build_json_schema(self, parameters: dict) -> dict:
        """从 ToolSpec.parameters 构造 JSON Schema。"""
        properties = {}
        for key, info in parameters.items():
            properties[key] = {
                "type": info.get("type", "string"),
                "description": info.get("description", ""),
            }
        return {
            "type": "object",
            "properties": properties,
            "required": list(parameters.keys()),
        }
```

## 实现步骤

### 步骤 1: MCPAdapter

**涉及文件**: `src/transbridge/smart_assistant/mcp/adapter.py`（新建）

**实现要点**:
- ToolSpec → MCP Tool JSON Schema 转换
- 安全过滤：admin 需白名单 / write 按策略 deny/allow
- call_tool 包装 ToolSpec.execute() → MCP CallToolResult 格式

**边界条件**:
- ToolSpec.parameters 为空 → inputSchema.properties 为空对象
- 工具执行抛异常 → 返回 isError: true，含异常信息

### 步骤 2: MCPServer

**涉及文件**: `src/transbridge/smart_assistant/mcp/server.py`（新建）

**实现要点**:
- stdio JSON-RPC 2.0 协议
- 方法路由：tools/list / tools/call
- 错误处理：JSON 解析错误 → -32700、方法不存在 → -32601
- stop() 方法：设置 _running=False 优雅退出
- 职责边界：Server 只做协议层，不关心工具注册/权限（由 Adapter 负责）

**边界条件**:
- stdin 空行 → 跳过
- JSON 解析失败 → 返回 Parse error（不崩溃）
- 多个请求并发 → 串行处理（stdio 天然串行）

### 步骤 3: mcp/__init__.py

**涉及文件**: `src/transbridge/smart_assistant/mcp/__init__.py`（新建）

### 步骤 4: INI 配置 + 启动集成

**涉及文件**: `src/transbridge/paratranz/config_manager.py`（修改）、应用启动入口（修改）

**实现要点**:
- `[mcp]` INI section:
  ```ini
  [mcp]
  enabled = false
  transport = stdio
  admin_tool_whitelist =
  write_tool_policy = deny
  ```
- 启动入口：读取 [mcp] section → enabled=true → 创建 MCPServer 实例 → 在独立线程中调用 run_stdio()
- 启动日志输出："MCP Server 已启动 (stdio)"

**边界条件**:
- [mcp] section 不存在 → 全部使用默认值（enabled=false）
- admin_tool_whitelist 为空字符串 → 不暴露任何 admin 工具

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/mcp/__init__.py` | 新建 | 子包入口 |
| `smart_assistant/mcp/server.py` | 新建 | MCPServer（stdio JSON-RPC） |
| `smart_assistant/mcp/adapter.py` | 新建 | MCPAdapter（ToolSpec 映射 + 安全过滤） |
| `paratranz/config_manager.py` | 修改 | [mcp] INI section 支持 |
| 应用启动入口 | 修改 | MCP Server 启动集成 |

## 风险与注意事项

- **风险**: MCPServer 运行在独立线程中占用 stdin/stdout，可能与 PyInstaller 打包后的标准 I/O 冲突 → 缓解：MCP 默认 disabled，用户显式启用
- **注意**: MCP 通道中无 UI 确认机制，write 工具默认 deny。admin 工具需显式白名单才能暴露——这是安全底线
- **注意**: MCPServer 的 ctx 在创建时绑定，整个生命周期不变。如需切换项目，需重启 MCP Server
- **注意**: JSON-RPC 的 id 字段由 Client 提供，Server 原样返回——不生成新的 id
