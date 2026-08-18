# ADR-012: 安全护栏、可观测性与 MCP Server

- **状态**: 已接受
- **日期**: 2026-05-10
- **决策者**: BuMing
- **对应需求**: FR7.13.5, FR7.13.8, FR7.13.9
- **关联 ADR**: [ADR-008](008-smart-assistant-code-layering.md)（子包结构）、[ADR-009](009-agent-file-memory-reflexion.md)（RetryHandler 注入模式）、[ADR-011](011-graph-orchestration-engine.md)（Graph 编排）

## Context

FR7.13 Phase 2 需要为 smart_assistant 新增三个横切关注点——安全护栏、可观测性、MCP Server——它们不改变现有的 DAG 执行与 Reflexion 重试逻辑，但在执行管道上叠加了新的行为层。

当前 ExecutionEngine（`smart_assistant/execution_engine.py`）采用注入模式集成外部横切组件：`_retry_handler` 由 ChatWidget 注入，执行时在 `_run_single()` 中触发 Reflexion 重试循环。Phase 2 三个新增关注点同样适用此模式——通过注入中间件链和遥测收集器，无需修改 DAG 拓扑排序或层级并行调度逻辑。

ToolSpec（`smart_assistant/tool_registry.py`）当前仅包含 `name`、`display_name`、`description`、`parameters`、`is_long_running`、`execute` 六个字段。安全护栏要求每个工具有明确的权限分级和资源上限，MCP Server 要求将 ToolSpec 映射为标准 MCP 协议格式，因此需要扩展 ToolSpec 数据类。

## Decision

### 1. 安全护栏：中间件链注入模式

**决策**: 复用 ADR-009 中 RetryHandler 的注入模式，在 `ExecutionEngine._run_single()` 中构建中间件链（Middleware Chain），对工具执行前后进行权限检查、输入校验和输出校验。

#### 1.1 中间件基类与守卫结果

```python
# src/transbridge/smart_assistant/guardrails/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

@dataclass
class GuardResult:
    """中间件守卫结果"""
    allowed: bool
    reason: str = ""
    modified_args: dict | None = None     # before: 可修改参数
    modified_result: dict | None = None   # after: 可过滤输出

class GuardMiddleware(ABC):
    """安全护栏中间件抽象基类"""

    @abstractmethod
    def before_execute(self, step: dict, ctx: Any) -> GuardResult:
        """工具执行前调用。返回 GuardResult.allowed=False 阻止执行。"""
        ...

    def after_execute(self, step: dict, result: dict, ctx: Any) -> GuardResult:
        """工具执行后调用。可过滤/修改输出。默认透传。"""
        return GuardResult(allowed=True)
```

#### 1.2 三个内置中间件

**PermissionGuard（权限检查）**:
```python
# src/transbridge/smart_assistant/guardrails/permission.py

class PermissionGuard(GuardMiddleware):
    """权限分级检查：read 放行，write 可选确认，admin 强制确认"""

    def __init__(self, registry):
        self._registry = registry

    def before_execute(self, step, ctx):
        spec = self._registry.get(step["tool"])
        if spec is None:
            return GuardResult(False, f"未知工具: {step['tool']}")
        if spec.permission == "admin":
            return GuardResult(False, "admin_required")
        if spec.permission == "write" and spec.require_confirmation:
            return GuardResult(False, "write_confirmation_required")
        return GuardResult(True)
```

**InputValidationGuard（输入校验）**:
- 类型检查：根据 ToolSpec.parameters 中声明的 JSON Schema 校验 args 类型
- 长度限制：字符串参数不超过 10000 字符，列表参数不超过 500 项
- 注入检测：检查 args 中是否包含危险模式（如 `../` 路径遍历、`__` 双下划线私有属性访问）

```python
class InputValidationGuard(GuardMiddleware):
    MAX_STRING_LENGTH = 10000
    MAX_LIST_ITEMS = 500

    def before_execute(self, step, ctx):
        args = step.get("args", {})
        # 1. 类型校验
        for key, schema in step.get("_param_schema", {}).items():
            if key in args:
                if not self._check_type(args[key], schema):
                    return GuardResult(False, f"参数类型错误: {key}")
        # 2. 长度限制
        for key, val in args.items():
            if isinstance(val, str) and len(val) > self.MAX_STRING_LENGTH:
                return GuardResult(False, f"参数过长: {key}")
            if isinstance(val, list) and len(val) > self.MAX_LIST_ITEMS:
                return GuardResult(False, f"列表参数过长: {key}")
        # 3. 注入检测
        if self._detect_path_traversal(args):
            return GuardResult(False, "检测到路径遍历模式")
        return GuardResult(True)
```

**OutputValidationGuard（输出校验）**:
- 类型检查：验证 execute 返回值包含 success/message/data 三个字段
- 大小限制：根据 ToolSpec.max_output_size 截断输出（默认 100KB）
- 敏感信息检测：检查输出中是否包含疑似 API key、token 等模式（`sk-`、`Bearer`、`eyJ`）——命中则脱敏处理

```python
class OutputValidationGuard(GuardMiddleware):
    SENSITIVE_PATTERNS = [
        (r'sk-[a-zA-Z0-9]{20,}', '[API_KEY_REDACTED]'),
        (r'Bearer\s+[a-zA-Z0-9\-_\.]{20,}', '[TOKEN_REDACTED]'),
    ]

    def after_execute(self, step, result, ctx):
        spec = self._registry.get(step["tool"])
        # 1. 截断过大输出
        max_size = spec.max_output_size if spec else 102400
        serialized = json.dumps(result, ensure_ascii=False)
        if len(serialized) > max_size:
            result["data"] = {"truncated": True, "message": "输出过大已截断"}
        # 2. 敏感信息脱敏
        result = self._redact_sensitive(result)
        return GuardResult(allowed=True, modified_result=result)
```

#### 1.3 ExecutionEngine 注入

在 `_run_single()` 中插入 before/after 中间件链：

```python
class ExecutionEngine(QObject):
    _middlewares: list[GuardMiddleware] = []  # 由 ChatWidget 注入

    # 确认信号
    step_requires_confirmation = pyqtSignal(str, str, list)
    # node_id, prompt, choices: ["allow_once", "deny", "allow_always"]

    def _run_single(self, step: dict) -> StepResult:
        step_id = step["id"]
        self.step_started.emit(step_id, step.get("tool", "?"))

        start = time.monotonic()

        # ── Phase 2: before 中间件链 ──
        for mw in self._middlewares:
            guard = mw.before_execute(step, self._ctx)
            if not guard.allowed:
                if guard.reason == "admin_required":
                    # 发射信号，等待用户确认
                    self.step_requires_confirmation.emit(
                        f"step_{step_id}",
                        f"工具 '{step['tool']}' 需要管理员权限",
                        ["allow_once", "deny"]
                    )
                    # 阻塞等待（通过 QEventLoop 或 Condition）
                    decision = self._wait_for_decision(f"step_{step_id}")
                    if decision != "allow_once":
                        return StepResult(
                            step_id=step_id, tool=step.get("tool", "?"),
                            success=False, message="权限不足",
                            duration_ms=0,
                        )
                elif guard.reason == "write_confirmation_required":
                    self.step_requires_confirmation.emit(
                        f"step_{step_id}",
                        f"确认执行写操作: {step['tool']}",
                        ["confirm", "deny"]
                    )
                    decision = self._wait_for_decision(f"step_{step_id}")
                    if decision != "confirm":
                        return StepResult(
                            step_id=step_id, tool=step.get("tool", "?"),
                            success=False, message="用户取消写操作",
                            duration_ms=0,
                        )
                else:
                    # 校验失败或其他拒绝原因
                    return StepResult(
                        step_id=step_id, tool=step.get("tool", "?"),
                        success=False, message=guard.reason or "安全策略拒绝",
                        duration_ms=0,
                    )
            # before 中间件可修改参数
            if guard.modified_args:
                step["args"] = guard.modified_args

        # ── 工具查找 + Reflexion 重试（现有逻辑不变） ──
        spec = self._registry.get(step["tool"])
        if spec is None:
            return StepResult(
                step_id=step_id, tool=step.get("tool", "?"),
                success=False, message=f"未知工具: {step['tool']}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        raw_result = None
        attempt = 0
        current_step = dict(step)
        while True:
            try:
                raw_result = spec.execute(current_step.get("args", step.get("args", {})), self._ctx)
                break
            except Exception as exc:
                if (self._retry_handler is None or
                        not self._retry_handler.should_retry(str(exc)) or
                        attempt >= self._retry_handler.MAX_RETRIES):
                    return StepResult(
                        step_id=step_id, tool=step.get("tool", "?"),
                        success=False, message=f"执行异常: {exc}",
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
                adjusted = self._retry_handler.analyze_and_adjust(
                    current_step, str(exc), attempt)
                if adjusted is None:
                    return StepResult(
                        step_id=step_id, tool=step.get("tool", "?"),
                        success=False, message=f"执行异常: {exc}",
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
                current_step = adjusted
                attempt += 1
                self.step_retrying.emit(step_id, attempt)

        # ── Phase 2: after 中间件链（逆序执行） ──
        after_result = dict(raw_result)
        for mw in reversed(self._middlewares):
            guard = mw.after_execute(step, after_result, self._ctx)
            if guard.modified_result:
                after_result = guard.modified_result
            if not guard.allowed:
                return StepResult(
                    step_id=step_id, tool=step.get("tool", "?"),
                    success=False, message=guard.reason or "输出校验拒绝",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

        return StepResult(
            step_id=step_id, tool=step.get("tool", "?"),
            success=after_result.get("success", True),
            message=after_result.get("message", ""),
            data=after_result.get("data"),
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    def _wait_for_decision(self, node_id: str) -> str:
        """阻塞等待用户确认（由 provide_decision 唤醒）。"""
        # 实现：threading.Condition / QEventLoop
        ...

    def provide_decision(self, node_id: str, choice: str) -> None:
        """ChatWidget 调用：提供用户确认选择，唤醒 _wait_for_decision。"""
        ...
```

**理由**:
- 中间件链注入模式与 RetryHandler 一致，开发者无需学习新范式
- before/after 分离：before 链负责权限控制和输入校验（阻止非法执行），after 链负责输出校验和脱敏（过滤敏感数据）
- after 链逆序遍历，符合洋葱模型语义（先 before 的后 after）
- 确认信号机制通过现有 pyqtSignal 管道实现，无需引入新的事件总线
- 每个中间件单一职责，可按需组合（如仅启用 PermissionGuard + InputValidationGuard，禁用 OutputValidationGuard）

#### 1.4 ToolSpec 权限扩展

```python
@dataclass
class ToolSpec:
    name: str
    display_name: str
    description: str
    parameters: dict                # JSON Schema
    is_long_running: bool = False
    execute: Callable | None = None
    permission: str = "read"        # NEW: "read" | "write" | "admin"
    require_confirmation: bool = False  # NEW: write 级可配置额外确认
    max_output_size: int = 102400   # NEW: 输出大小限制(bytes)，默认100KB
```

**权限分级**:

| 级别 | 行为 | 示例工具 |
|------|------|---------|
| `read` | 直接放行，无需确认 | lookup_terms, get_collection_summary, check_quality |
| `write` | 放行；若 `require_confirmation=True` 则弹出确认 | translate_entries, export_json |
| `admin` | 始终要求确认，UI 端弹出授权对话框 | write_back, 未来可能的 delete_collection 等 |

**现有工具分级映射**（Phase 2 实现时更新 `_register_v1_tools`）:
- `lookup_terms` → `read`
- `get_collection_summary` → `read`
- `check_quality` → `read`
- `translate_entries` → `write`, `require_confirmation=False`（翻译是核心功能，高频操作）
- `export_json` → `write`, `require_confirmation=True`（文件写入需确认）
- `write_back` → `admin`（修改 ESP/EET/XT 文件，最高风险）

### 2. 可观测性：pyqtSignal 遥测管道

**决策**: 不新建独立遥测系统。所有观测数据通过现有 ExecutionEngine 信号管道收集，构建 `ObservabilityCollector` 作为数据聚合层。

#### 2.1 数据模型

```python
# src/transbridge/smart_assistant/observability/models.py

from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class TokenStats:
    input_tokens: int = 0
    output_tokens: int = 0
    by_model: dict = field(default_factory=dict)  # "claude-opus-4-7" -> TokenStats

    def add(self, other: "TokenStats") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class ReActRound:
    round_num: int
    llm_input_tokens: int
    llm_output_summary: str     # 截断至 200 字符
    tools: list[str]            # 本轮调用的工具列表
    duration_ms: int


@dataclass
class ToolCallRecord:
    timestamp: str              # ISO format
    tool_name: str
    input_summary: str          # 截断至 500 字符
    output_summary: str         # 截断至 500 字符
    duration_ms: int
    success: bool
    retry_count: int


@dataclass
class ConversationTrace:
    conv_id: str
    started_at: str
    rounds: list[ReActRound] = field(default_factory=list)
    tools_called: list[ToolCallRecord] = field(default_factory=list)
    token_stats: TokenStats = field(default_factory=TokenStats)
```

#### 2.2 ObservabilityCollector

```python
# src/transbridge/smart_assistant/observability/collector.py

class ObservabilityCollector:
    """观测数据收集器。注入到 ExecutionEngine 中，订阅信号管道。"""

    def __init__(self, storage_dir: Path, retention_days: int = 30):
        self._storage_dir = storage_dir
        self._retention_days = retention_days
        self._session_token_total = TokenStats()
        self._current_traces: dict[str, ConversationTrace] = {}

    def on_conversation_started(self, conv_id: str) -> None:
        trace = ConversationTrace(
            conv_id=conv_id,
            started_at=datetime.now().isoformat(),
        )
        self._current_traces[conv_id] = trace

    def on_round_started(self, conv_id: str, round_num: int) -> ReActRound:
        return ReActRound(
            round_num=round_num,
            llm_input_tokens=0,
            llm_output_summary="",
            tools=[],
            duration_ms=0,
        )

    def on_step_started(self, step_id: int, tool_name: str) -> None: ...

    def on_step_finished(self, result: StepResult) -> ToolCallRecord:
        record = ToolCallRecord(
            timestamp=datetime.now().isoformat(),
            tool_name=result.tool,
            input_summary="",
            output_summary=result.message[:500],
            duration_ms=result.duration_ms,
            success=result.success,
            retry_count=0,
        )
        self._session_token_total.add(...)
        return record

    def on_llm_chunk(self, chunk: dict) -> None:
        """统计 token 用量。需 ChatWorker 在收到 usage 信息时调用。"""
        ...

    def get_conversation_trace(self, conv_id: str) -> ConversationTrace | None:
        return self._current_traces.get(conv_id)

    def flush(self, conv_id: str) -> None:
        """持久化单次对话追踪到磁盘。"""
        trace = self._current_traces.pop(conv_id, None)
        if trace is None:
            return
        path = self._storage_dir / f"{conv_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(trace), f, ensure_ascii=False, indent=2, default=str)

    def cleanup_expired(self) -> int:
        """清理超过保留期的追踪文件，返回删除数量。"""
        ...
```

#### 2.3 存储路径与保留策略

```
data/projects/{project}/{variant}/observability/
├── {conv_id_1}.json
├── {conv_id_2}.json
└── ...
```

**保留策略**: 最近 30 天，过期自动清理。`ObservabilityCollector` 初始化时调用 `cleanup_expired()`，或在应用关闭时统一触发。

**理由**:
- 复用现有 pyqtSignal 管道，零额外依赖。ExecutionEngine 已有 `step_started`、`step_finished`、`step_retrying` 信号，Collector 仅需连接并聚合
- 按项目/变体隔离存储，与 MemoryStore 的存储模式一致（见 ADR-009）
- 30 天保留策略避免磁盘无限增长；追踪文件为 JSON 格式，便于用户自行查阅或导入外部分析工具
- Collector 独立于 ExecutionEngine 和 UI，可单独测试
- 降级安全：若 storage_dir 不可写，Collector 静默跳过持久化，不影响核心功能

### 3. MCP Server：stdio + ToolSpec 映射

**决策**: MCP Server 通过标准输入输出流（stdio）对外暴露 ToolRegistry 中的工具，将 ToolSpec 映射为 MCP 协议格式。

#### 3.1 子包结构

```
src/transbridge/smart_assistant/mcp/          # NEW
├── __init__.py
├── server.py              # MCPServer 主类（stdio JSON-RPC）
└── adapter.py             # ToolSpec ↔ MCP Tool 映射
```

#### 3.2 MCPServer 主类

```python
# src/transbridge/smart_assistant/mcp/server.py

import sys
import json

class MCPServer:
    """MCP 协议服务端，通过 stdio 与外部 MCP Client 通信。"""

    def __init__(self, registry, ctx, config: dict | None = None):
        self._registry = registry      # ToolRegistry
        self._ctx = ctx                # AppContext
        self._config = config or {}    # [mcp] INI section

    def run_stdio(self) -> None:
        """从 stdin 逐行读取 JSON-RPC 请求，处理后写入 stdout。"""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                response = self._handle_request(request)
                if response is not None:
                    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
            except json.JSONDecodeError:
                sys.stdout.write(json.dumps({
                    "jsonrpc": "2.0",
                    "error": {"code": -32700, "message": "Parse error"},
                    "id": None,
                }) + "\n")
                sys.stdout.flush()

    def _handle_request(self, request: dict) -> dict | None:
        method = request.get("method")
        req_id = request.get("id")

        if method == "tools/list":
            return self._list_tools(req_id)
        if method == "tools/call":
            return self._call_tool(request.get("params", {}), req_id)
        if method == "initialize":
            return self._initialize(req_id)

        return {
            "jsonrpc": "2.0",
            "error": {"code": -32601, "message": f"Method not found: {method}"},
            "id": req_id,
        }

    def _initialize(self, req_id) -> dict:
        return {
            "jsonrpc": "2.0",
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "TransBridge", "version": "0.12.0"},
                "capabilities": {"tools": {}},
            },
            "id": req_id,
        }

    def _list_tools(self, req_id) -> dict:
        tools = []
        for spec in self._registry.list_all():
            if not self._is_exposed(spec):
                continue
            tools.append(self._adapter.to_mcp_tool(spec))
        return {
            "jsonrpc": "2.0",
            "result": {"tools": tools},
            "id": req_id,
        }

    def _call_tool(self, params: dict, req_id) -> dict:
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        spec = self._registry.get(tool_name)
        if spec is None:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"},
                "id": req_id,
            }
        if not self._is_exposed(spec):
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32603, "message": f"Tool not exposed: {tool_name}"},
                "id": req_id,
            }
        try:
            result = spec.execute(arguments, self._ctx)
            return {
                "jsonrpc": "2.0",
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                    "isError": not result.get("success", True),
                },
                "id": req_id,
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
                "id": req_id,
            }

    def _is_exposed(self, spec) -> bool:
        """根据配置决定工具是否对外暴露。"""
        if spec.permission == "read":
            return True
        if spec.permission == "admin":
            if not self._config.get("expose_admin_tools", False):
                return False
            whitelist = self._config.get("admin_tool_whitelist", "")
            if whitelist:
                return spec.name in [n.strip() for n in whitelist.split(",")]
            return True
        if spec.permission == "write":
            policy = self._config.get("write_tool_policy", "allow")
            if policy == "deny":
                return False
            if policy == "require_confirm":
                # MCP 通道无 UI 确认机制，静默拒绝
                return False
        return True
```

#### 3.3 ToolSpec ↔ MCP 映射适配器

```python
# src/transbridge/smart_assistant/mcp/adapter.py

class MCPAdapter:
    """ToolSpec 与 MCP Tool 定义之间的双向映射。"""

    def to_mcp_tool(self, spec) -> dict:
        """将 ToolSpec 转换为 MCP tools/list 响应中的 tool 对象。"""
        return {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": {
                "type": "object",
                "properties": spec.parameters or {},
                "required": self._extract_required(spec.parameters),
            },
        }

    def _extract_required(self, parameters: dict) -> list[str]:
        """从 JSON Schema parameters 中提取 required 字段。"""
        if not parameters:
            return []
        required = []
        for key, schema in parameters.items():
            if isinstance(schema, dict) and schema.get("required"):
                required.append(key)
        return required
```

**协议映射**:
- MCP `initialize` → 返回 `protocolVersion: "2024-11-05"`，`capabilities.tools`
- MCP `tools/list` → `ToolRegistry.list_all()` → 经 `_is_exposed()` 过滤 → MCPAdapter 转换为 MCP Tool JSON Schema
- MCP `tools/call` → `ToolRegistry.get(name).execute(args, ctx)` → 包装为 MCP content 数组

#### 3.4 安全约束

由于 MCP 通道通过 stdio 运行，无 UI 交互能力，安全策略与 GUI 模式有所区别：

| 权限级别 | GUI 模式 | MCP 模式 |
|---------|---------|---------|
| `read` | 直接放行 | 直接暴露 |
| `write` | 放行（可选确认弹窗） | 可配置 `allow`/`deny`/`require_confirm`；`require_confirm` 等价于 `deny`（无 UI 通道） |
| `admin` | 弹窗确认后放行 | 默认不暴露；需 `expose_admin_tools=true` + `admin_tool_whitelist` 白名单 |

#### 3.5 INI 配置

```ini
[mcp]
enabled = false
transport = stdio
expose_admin_tools = false
admin_tool_whitelist =
write_tool_policy = allow    # allow | deny | require_confirm (等效 deny)
```

**理由**:
- stdio 传输是最通用的 MCP 传输方式，无需网络端口绑定或进程间通信，外部 MCP Client（如 Claude Desktop、VS Code Copilot）通过子进程方式接入
- 安全策略在 MCP 通道中更保守：admin 工具默认不暴露，write 工具可配置拒绝
- ToolSpec 到 MCP Tool 的映射通过独立 adapter 实现，方便未来支持 MCP 协议升级或额外的传输方式（如 HTTP SSE）

### 4. 子包结构汇总

在 ADR-008 已定义的 smart_assistant 子包基础上新增 Phase 2 三个子包：

```
src/transbridge/smart_assistant/
├── (现有 7 文件不变)
├── skills/               # Phase 1 (ADR-008)
├── file_parser/          # Phase 1 (ADR-008)
├── memory/               # Phase 1 (ADR-008)
├── reflexion/            # Phase 1 (ADR-008)
├── agents/               # Phase 2 (ADR-008 更新)
├── guardrails/           # NEW: Phase 2 (本 ADR)
│   ├── __init__.py
│   ├── base.py           # GuardMiddleware ABC + GuardResult
│   ├── permission.py     # PermissionGuard
│   ├── input_validator.py # InputValidationGuard
│   └── output_validator.py # OutputValidationGuard
├── observability/        # NEW: Phase 2 (本 ADR)
│   ├── __init__.py
│   ├── collector.py      # ObservabilityCollector
│   └── models.py         # ConversationTrace / ToolCallRecord / TokenStats / ReActRound
├── mcp/                  # NEW: Phase 2 (本 ADR)
│   ├── __init__.py
│   ├── server.py         # MCPServer
│   └── adapter.py        # MCPAdapter: ToolSpec ↔ MCP 映射
├── graph_types.py        # Phase 2 (ADR-011)
└── graph_executor.py     # Phase 2 (ADR-011)
```

**子包间依赖**:

```
guardrails → tool_registry (ToolSpec 查询)
observability → execution_engine (订阅信号)
mcp → tool_registry + AppContext
```

三个子包之间无相互依赖，各自独立。

## 备选方案

### 备选方案 A：安全护栏使用独立事件总线

不使用 ExecutionEngine 注入，而是使用全局 EventBus（如 `pyqtSignal` 全局调度）在工具调用前后广播事件，中间件订阅事件进行拦截。

**拒绝理由**: 引入了隐式控制流，调试困难。注入模式使中间件链在 `_run_single()` 中可见、可追踪，符合 ADR-009 已有范式，开发者无需学习新的事件调度机制。

### 备选方案 B：可观测性使用 OpenTelemetry SDK

引入 `opentelemetry-api` + `opentelemetry-sdk` 依赖，按标准 OTel Span/Trace 协议实现追踪。

**拒绝理由**: 引入重量级依赖（~5MB 额外打包体积），且 TransBridge 是桌面应用非分布式系统，不需要 OTel 的导出器（Jaeger/Zipkin）和跨服务传播。pyqtSignal 管道足以覆盖单进程桌面的遥测需求，且零额外依赖。

### 备选方案 C：MCP Server 使用 HTTP/SSE 传输

MCP Server 通过本地 HTTP 端口（如 `localhost:9527`）对外暴露，使用 SSE 推送。

**拒绝理由**: 增加端口管理和网络配置的复杂度；桌面应用绑定端口可能与其他服务冲突；stdio 是 MCP 官方推荐的最简传输方式，且无需处理 CORS、防火墙等网络问题。

### 备选方案 D：MCP Server 独立进程

MCP Server 作为完全独立的 Python 进程（独立于 TransBridge GUI），通过共享内存或文件系统与 ToolRegistry 通信。

**拒绝理由**: 引入进程间通信复杂度。子进程模式（`python -m transbridge.smart_assistant.mcp`）已经实现进程隔离，MCP Client 通过子进程 stdio 接入，无需额外进程管理。

## Consequences

- **正面**:
  - 安全护栏通过中间件链注入，不改动 DAG 拓扑逻辑，与 RetryHandler 复用同一范式
  - 可观测性零额外依赖，复用现有 pyqtSignal 管道，JSON 文件持久化便于用户审计
  - MCP Server 使 TransBridge 工具可被外部 AI 客户端（Claude Desktop 等）调用，扩展了工具的适用范围
  - 三个子包独立解耦，可按需启用/禁用（如用户关闭 MCP，不影响护栏和遥测）

- **负面**:
  - ExecutionEngine._run_single() 方法体增长（新增 2 段中间件链 + 确认等待逻辑），复杂度上升
  - PermissionGuard 的确认等待机制通过 `_wait_for_decision` 阻塞线程，需注意与 ThreadPoolExecutor 线程池交互——确保 `provide_decision` 从 UI 线程调用 `Condition.notify` 时线程安全
  - MCP Server 的 stdio 模式要求 MCP Client 以子进程方式启动 TransBridge，启动时间受 PyQt6 初始化影响（~2s）

- **风险**:
  - PermissionGuard 阻塞等待确认可能导致 ThreadPoolExecutor 线程耗尽（4 worker），若用户长期不响应确认弹窗 → 缓解：设置 60s 超时，超时自动拒绝
  - 敏感信息检测（OutputValidationGuard）基于正则模式，可能误杀合法输出 → 缓解：仅脱敏不拒绝，记录日志供用户审查
  - MCP stdio 模式下 stdout 被 JSON-RPC 占用，所有日志必须输出到 stderr 或文件 → 约定：MCP 模式下 `logging.getLogger` 全部输出到 stderr

---

### 更新: 2026-05-14 - ToolResult 观察消息序列化格式约定

**对应需求**: FR7.17 | **对应 Epic**: llm-chat（Story-10）

#### 背景

当前 `OutputValidationGuard.after_execute()` 已对 `ToolResult.data` 做大小检查和脱敏，但工具执行结果到 LLM 观察消息的序列化没有统一约定。`ToolExecutionHandler._handle_result()` 仅提取 `message` 字符串，`data` 被完全丢弃。需要定义从 `ToolResult` 到 LLM 可解析文本的序列化管线格式。

#### 决策

**1. 序列化逻辑归属 ToolResult**

`to_observation(tool_name, max_chars=2000) -> str` 方法直接定义在 `ToolResult` 数据类上，遵循已有 `to_dict()` 的先例。不创建独立的序列化器类 —— 序列化逻辑与数据结构紧耦合（需感知常见列表键名以触发智能摘要），外部化只会增加不必要的参数传递。

**2. 扩展字段为正式 dataclass 字段**

新增三个可选字段作为 `ToolResult` 的正式 dataclass 字段（而非 `data` 字典的约定键名）：

```python
pagination: dict | None = None       # {"page": 1, "total_pages": 5, "has_more": true, "total_count": 200}
execution_meta: dict | None = None   # {"duration_ms": 850, "attempt": 2}
tool_suggestions: list[str] | None   # ["get_visible_entries", "edit_translation"]
```

理由：IDE 类型检查支持、`to_observation()` 可特化处理（pagination 独立行、suggestions 紧凑逗号分隔格式）、避免字符串键拼写错误。

**3. 三级截断管线**

```
Layer 1: ToolResult._serialize_data(max_chars) → 大数据智能摘要（列表→count+sample）
Layer 2: ToolResult.to_observation(max_chars=2000) → 总长度控制，换行边界裁剪
Layer 3: ConversationManager.add_observation() → 兜底安全网
```

与 OutputValidationGuard 的关系：OutputValidationGuard 在 `after_execute` 中检查 `data` 的字节大小并脱敏 → `to_observation()` 在其之后运行，对已验证的数据进行格式化序列化。

**4. 观察消息格式**

```
[OK] tool_name: 人读摘要
  data: {"key": "value", ...}
  pagination: {...}
  meta: {...}
  suggest: tool1, tool2
```

采用 `key: value` 行格式，每行一个维度，LLM 天然可解析。`data` 行使用紧凑 JSON（`separators=(",", ":")`）。

#### 备选方案

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. 独立观察序列化器类 | 职责单一，易于单独测试 | 需要传递 ToolResult 内部结构知识，增加一层间接性 |
| B. 新字段通过 data 约定键名 | 不改 ToolResult 签名 | 无类型检查，拼写错误静默失败，无法特化格式化 |
| C. 在 ConversationManager 中集中序列化 | 集中管理所有消息格式 | ConversationManager 需要了解 ToolResult 内部结构，违反单一职责 |

#### 影响

- **接口变更**: `ToolResult` 新增 3 个可选字段（`pagination`/`execution_meta`/`tool_suggestions`）+ 2 个方法（`to_observation()`/`_serialize_data()`）。`ToolExecutionHandler._handle_result()` 改为调用 `to_observation()` 生成观察文本。`ConversationManager.add_observation()` 截断逻辑优化为换行感知。
- **向后兼容**: 完全兼容 — 所有新字段默认 None，不传 `data` 的 ToolResult 输出与之前相同的状态行格式。
- **依赖变更**: 零新依赖（仅 `import json`）。

### 更新：2026-08-18 — 独立 stdio MCP、RuntimeContext 与共享安全策略（已接受）

本更新以 [ADR-016](016-modular-monolith-application-composition.md) 部分取代本 ADR 的 MCPServer/AppContext 构造、固定协议版本、GUI 内线程启动和自定义逐请求 token 设计：

- MCP 由独立 `transbridge-mcp` console entry point 启动，使用 headless Composition Root；GUI 不读取 stdin。
- MCP adapter 调用 application use case，并建立 owner/权限/授权路径受限的 RuntimeContext；不得把空 AppContext 传给 GUI 工具。
- 协议版本和 capabilities 在 initialize 生命周期协商，不硬编码单一版本；stdout 只输出合法 MCP 消息，日志写 stderr。
- 官方 stdio 规范要求客户端把 server 作为子进程启动；stdio 凭据应从环境获取，而不是套用 HTTP 授权流。参考：[MCP transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)、[MCP lifecycle](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle)、[MCP authorization](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)。
- admin/write 工具默认不暴露；没有 HITL 通道时需要确认的操作返回明确拒绝。路径授权在 canonical path/symlink 解析后执行，GUI/Agent/MCP 复用同一 policy。
- ToolResult/观察消息保留结构化状态与诊断；截断只影响展示摘要，不得删除执行所需 schema 或把 partial/failed 改为成功。
