# Story 01: Blocker 安全护栏修复

**所属方案**: `plans/smart-assistant-qa-fix/plan.md`
**技术模块**: `ui/tools/smart_assistant/`、`smart_assistant/`（execution_engine + guardrails）
**状态**: 已确认
**创建日期**: 2026-05-12
**覆盖问题**: B1（ReAct 绕过安全护栏）、B3（ExecutionEngine 忽略 middlewares 参数）

## 前置依赖

### 上游 Story
无 — 本 Story 为修复链起点，不依赖其他 Story。

### 引用的架构决策
- **ADR-012 §1** (安全护栏中间件链注入模式): 所有工具执行应通过 PermissionGuard → InputValidationGuard → OutputValidationGuard 链
- **ADR-012 §1.3** (ExecutionEngine 注入): `_middlewares: list[GuardMiddleware]` 由 ChatWidget 注入，`_run_single()` 中 before/after 链执行
- **ADR-008 §2** (Import 规范): UI → 后端使用绝对导入

## 验收标准

（从 plan 原样复制）

- [ ] ReAct 模式下工具调用走 `execute_with_guardrails()`，PermissionGuard/InputValidationGuard/OutputValidationGuard 全部生效
- [ ] `ExecutionEngine.__init__` 正确使用传入的 `middlewares` 参数构建 `_guards` 列表
- [ ] 用户可在配置中禁用某类中间件且实际生效
- [ ] admin 级工具（write_to_esp/eet/xt）在自动模式下仍需用户确认
- [ ] 路径遍历检测、扩展名白名单、输出脱敏在 ReAct 路径下正常拦截

## 数据流

```
用户消息 → ChatWidget._on_send()
  → LLM 返回 tool_use 响应
  → ToolCard 渲染按钮
  → 用户点击「执行」→ ToolCard._on_execute()
  → chat_widget._on_tool_executed(step)
      │
      │ 【当前 B1 漏洞】
      │   spec.execute(args, self._ctx)              ← 裸调，绕过全部护栏
      │
      │ 【修复后】
      │   exec_ctx = ExecutionContext(                  ← 包装 AppContext + TaskManager
      │       app_context=self._ctx,
      │       task_manager=TaskManager()
      │   )
      │   result = execute_with_guardrails(             ← 统一入口
      │       spec, args, exec_ctx,
      │       middlewares=self._middlewares             ← 传递用户配置的护栏链
      │   )
      │   # result 是 ToolResult，适配 _handle_tool_result
      │
      ▼
  → _handle_tool_result(step, result)

计划模式路径（已正常，不改）:
  _on_plan_confirmed(steps)
    → ExecutionEngine(ToolRegistry, ctx, middlewares=middlewares)
        │
        │ 【当前 B3 漏洞】
        │   _build_guard_chain()                     ← 忽略传入的 middlewares
        │
        │ 【修复后】
        │   if middlewares:
        │       self._guards = list(middlewares)
        │   else:
        │       self._guards = _build_guard_chain() or []
        │
    → engine.execute(steps) → _run_single(step) → 完整护栏链
```

## 关键接口

### 修改: `execute_with_guardrails()`

```python
# src/transbridge/smart_assistant/tools/base.py

def execute_with_guardrails(
    spec,
    args: dict,
    ctx: ExecutionContext,
    middlewares: list | None = None,  # NEW: 可选自定义中间件链
) -> ToolResult:
    """统一工具执行入口，GUI 和 MCP 共享同一条中间件链。

    链: PermissionGuard → InputValidationGuard → 工具执行 → OutputValidationGuard
    B6+B1: 消除 GUI/MCP 安全分叉，支持自定义护栏链。
    """
    guards = middlewares if middlewares is not None else _build_guard_chain()
    # 注意: middlewares 可能是 [PermissionGuard(), InputValidationGuard(), OutputValidationGuard()]
    # 也可能是用户裁剪后的链（如仅 [InputValidationGuard()]）
    ...
```

### 修改: `ChatWidget.__init__()`

```python
# src/transbridge/ui/tools/smart_assistant/chat_widget.py

class ChatWidget(QWidget):
    def __init__(self, ctx, ...):
        ...
        self._middlewares: list | None = None  # 延迟构建，首次使用时从配置读取
        ...

    def _ensure_middlewares(self) -> list:
        """延迟构建护栏中间件链，遵循用户配置。"""
        if self._middlewares is not None:
            return self._middlewares
        cfg = LLMConfig.load_from_file()
        middlewares = []
        if cfg.guardrails_enable_input_validation:
            middlewares.append(InputValidationGuard(cfg.guardrails_max_input_size))
        middlewares.append(PermissionGuard(
            enable_admin_confirm=cfg.guardrails_enable_admin_confirm,
            write_require_confirm=cfg.guardrails_write_require_confirm,
        ))
        if cfg.guardrails_enable_output_validation:
            middlewares.append(OutputValidationGuard())
        self._middlewares = middlewares
        return middlewares
```

### 修改: `_on_tool_executed()`（ReAct 路径修复）

```python
# chat_widget.py:471-482

def _on_tool_executed(self, step: dict) -> None:
    tool_name = step.get("tool", "?")
    from src.transbridge.smart_assistant.tool_registry import ToolRegistry
    spec = ToolRegistry.get(tool_name)
    if spec and spec.execute:
        try:
            # B1 FIX: 走 execute_with_guardrails 而非裸调 spec.execute
            exec_ctx = ExecutionContext(
                app_context=self._ctx,
                task_manager=TaskManager()
            )
            result = execute_with_guardrails(
                spec, step.get("args", {}), exec_ctx,
                middlewares=self._ensure_middlewares()
            )
        except Exception as exc:
            result = ToolResult.fail(str(exc))
    else:
        result = ToolResult.fail(f"未知工具: {tool_name}")
    self._handle_tool_result(step, result)
```

### 修改: `ExecutionEngine.__init__()`（B3 修复）

```python
# execution_engine.py:34-44

def __init__(self, tool_registry, ctx, parent=None, middlewares=None):
    super().__init__(parent)
    self._registry = tool_registry
    self._ctx = ctx
    self._cancelled = threading.Event()
    self._retry_handler = None
    # B3 FIX: 优先使用传入的 middlewares，无传参时 fallback
    if middlewares:
        self._guards = list(middlewares)
    else:
        self._guards = _build_guard_chain() or []
    self._pending_decisions: dict[str, str] = {}
```

### 适配: `_handle_tool_result()` — ToolResult 兼容

```python
# chat_widget.py:502-516

def _handle_tool_result(self, step: dict, result) -> None:
    """统一处理工具执行结果。接受 ToolResult 或 dict。"""
    tool_name = step.get("tool", "?")
    if isinstance(result, ToolResult):
        success = result.success
        message = result.message
    elif isinstance(result, dict):
        success = result.get("success")
        message = result.get("message", result.get("error", ""))
    else:
        success = False
        message = str(result)

    if success:
        msg = f"[OK] {tool_name}: {message or '完成'}"
    else:
        msg = f"[FAIL] {tool_name}: {message or '失败'}"
    self.add_system_message(msg)
    self._conversation.add_observation(tool_name, msg)
    if self._check_react_depth():
        self._run_llm_round()
```

## 实现步骤

### 步骤 1: 修复 `execute_with_guardrails` 支持自定义 middlewares

**涉及文件**: `src/transbridge/smart_assistant/tools/base.py`（修改）

**实现要点**:
- 添加 `middlewares: list | None = None` 参数
- `guards = middlewares if middlewares is not None else _build_guard_chain()`
- 向后兼容：不传 middlewares 时行为完全不变

**边界条件**:
- `middlewares` 为空列表 `[]` → 不应用任何护栏（用户明确禁用所有护栏）
- `middlewares` 为 `None` → 使用 `_build_guard_chain()` 默认链
- `_build_guard_chain()` 返回 `None`（ImportError）→ 拒绝执行

**伪代码**:
```python
def execute_with_guardrails(spec, args, ctx, middlewares=None):
    guards = middlewares if middlewares is not None else _build_guard_chain()
    if guards is None:
        return ToolResult.fail("安全护栏不可用")
    # ... 后续链逻辑不变
```

**测试策略**:
- 传入自定义 middlewares → 验证使用自定义链
- 不传 middlewares → 验证使用默认链
- 传入空列表 → 验证直接执行工具不经过护栏

---

### 步骤 2: ChatWidget 添加 middlewares 延迟构建 + ReAct 路径修复

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/chat_widget.py`（修改）

**实现要点**:
- 新增 `_middlewares` 属性和 `_ensure_middlewares()` 方法
- `_on_tool_executed` 改为调用 `execute_with_guardrails`
- 构造 `ExecutionContext(self._ctx, TaskManager())`
- `_handle_tool_result` 适配 `ToolResult` 类型（统一处理 ToolResult 和 dict）
- `_on_plan_confirmed` 复用 `_ensure_middlewares()`

**边界条件**:
- `ToolRegistry.get(tool_name)` 返回 None → `ToolResult.fail("未知工具")`
- `spec.execute` 抛出异常 → 被外层 try/except 捕获，包装为 `ToolResult.fail`
- `execute_with_guardrails` 中权限检查失败 → `ToolResult.fail("权限不足")` → 显示给用户
- `LLMConfig.load_from_file()` 失败 → `_ensure_middlewares` 返回默认空链并记录错误
- 原有的 `_on_plan_confirmed` 中的 middlewares 构建代码 → 改为调用 `_ensure_middlewares()`

**伪代码**:
```python
def _ensure_middlewares(self):
    if self._middlewares is not None:
        return self._middlewares
    try:
        cfg = LLMConfig.load_from_file()
    except Exception:
        self._middlewares = _build_guard_chain() or []
        return self._middlewares
    # build from config...
    self._middlewares = middlewares
    return middlewares

def _on_plan_confirmed(self, steps):
    middlewares = self._ensure_middlewares()
    self._engine = ExecutionEngine(ToolRegistry, self._ctx, middlewares=middlewares)
    ...
```

**测试策略**:
- ReAct 模式下执行 write 级工具 → 验证 InputValidationGuard 生效
- ReAct 模式下执行 admin 级工具 → 验证 PermissionGuard 生效
- 关闭输入校验配置 → 验证 InputValidationGuard 不生效
- 工具执行失败 → 验证错误消息正确显示
- `_on_plan_confirmed` 仍然正常工作

---

### 步骤 3: 修复 ExecutionEngine 使用传入的 middlewares

**涉及文件**: `src/transbridge/smart_assistant/execution_engine.py`（修改）

**实现要点**:
- `__init__` 中 `if middlewares: self._guards = list(middlewares)`
- 否则 fallback 到 `_build_guard_chain() or []`
- 变量名: 使用 `self._guards`（与 `_run_single` 中 for 循环一致）

**边界条件**:
- `middlewares=None` → fallback 默认链（向后兼容）
- `middlewares=[]` → 空 _guards，`_run_single` 中 for 循环直接跳过
- `middlewares=[InputValidationGuard()]` → 仅应用输入校验
- Guard 模块 ImportError → `_build_guard_chain()` 返回 None → `self._guards = []`

**伪代码**:
```python
def __init__(self, tool_registry, ctx, parent=None, middlewares=None):
    ...
    if middlewares is not None:
        self._guards = list(middlewares)
    else:
        self._guards = _build_guard_chain() or []
    ...
```

**测试策略**:
- 传入定制 middlewares → 验证 `self._guards` 正确
- 不传 middlewares → 验证 fallback 到默认链
- 传入空列表 → 验证 `_run_single` 跳过护栏

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/smart_assistant/tools/base.py` | 修改 | `execute_with_guardrails` 添加 `middlewares` 参数 |
| `src/transbridge/ui/tools/smart_assistant/chat_widget.py` | 修改 | `_on_tool_executed` 改用 `execute_with_guardrails`；`_ensure_middlewares` 延迟构建；`_handle_tool_result` 适配 ToolResult |
| `src/transbridge/smart_assistant/execution_engine.py` | 修改 | `__init__` 使用传入的 `middlewares` 参数 |

## 风险与注意事项

- **风险**: `execute_with_guardrails` 添加参数后，其他调用方（如 MCP server）未传 `middlewares` → 行为不变（默认 None → fallback），向下兼容
- **风险**: PermissionGuard 在 ReAct 模式下弹窗确认 → `execute_with_guardrails` 是同步函数，无法 emit pyqtSignal 等待用户响应 → **缓解**: 对于 `requires_confirmation` 的情况，当前 `execute_with_guardrails` 直接返回 `ToolResult.fail("权限不足")`，由 LLM 感知并提示用户切换到手动模式；后续 Story-04 将 HITL 支持加入 `execute_with_guardrails`
- **注意**: `_handle_tool_result` 需要同时兼容 `ToolResult` 和 `dict` 两种返回类型，因为 `_auto_execute_steps` 路径可能直接传 dict
