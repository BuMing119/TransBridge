# Story 08: 安全护栏中间件

**所属方案**: `plans/agent-upgrade/plan.md`
**技术模块**: smart_assistant/guardrails
**状态**: 已确认
**创建日期**: 2026-05-10

## 前置依赖

### 上游 Story
- Story-06（同 plan）：必须已完成 → ToolSpec 数据类就绪（本 Story 扩展 ToolSpec 字段）
- 本 Story 与 S09-S10（Graph 引擎）独立，可并行开发

### 引用的架构决策
- ADR-012: 中间件链注入模式 + GuardMiddleware ABC + 权限三级 + 确认信号
- ADR-009: RetryHandler 注入模式（中间件注入复用的范式）

## 验收标准

- [ ] `GuardMiddleware` ABC（before_execute/after_execute）+ `GuardResult` 数据类
- [ ] `PermissionGuard`：从 ToolSpec.permission 读取权限级别（read/write/admin），admin 拒绝执行并触发确认，write 可配置需确认
- [ ] `InputValidationGuard`：类型检查 + 字符串长度限制（100KB）+ 注入模式检测（SQL/XSS/命令注入特征）
- [ ] `OutputValidationGuard`：类型检查 + 大小截断 + API key 等敏感信息脱敏
- [ ] `ToolSpec` 扩展：新增 `permission: str = "read"`、`require_confirmation: bool = False`、`max_output_size: int = 102400` 字段
- [ ] ExecutionEngine 注入 `_middlewares: list[GuardMiddleware]`，在 `_run_single()` 中构建 before→retry→after 中间件链
- [ ] `step_requires_confirmation` 信号：node_id + prompt + choices，UI 弹窗确认后通过 `provide_decision(node_id, choice)` 返回
- [ ] 护栏配置 `[guardrails]` INI section：enable_admin_confirm/enable_input_validation/enable_output_validation/max_input_size
- [ ] 所有现有工具 ToolSpec 标注 permission 字段（向后兼容：未标注默认 "read"）

## 数据流

```
ExecutionEngine._run_single(step)
  │
  ├─→ before 中间件链（顺序执行）
  │     ├─ PermissionGuard.before_execute(step, ctx)
  │     │   ├─ read → allowed=True
  │     │   ├─ write → require_confirmation? → 同 admin 流程
  │     │   └─ admin → step_requires_confirmation 信号
  │     │              → UI 弹窗等待用户确认
  │     │              → 确认: allowed=True / 拒绝: allowed=False, reason="用户拒绝"
  │     │
  │     └─ InputValidationGuard.before_execute(step, ctx)
  │           ├─ 参数类型检查（args 为 dict）
  │           ├─ 字符串长度 ≤ max_input_size (默认 100KB)
  │           └─ 注入模式检测: SQL("'; DROP"), XSS("<script>"), 命令注入("; rm")
  │              → 检测到注入: allowed=False, reason="检测到注入模式: ..."
  │
  ├─→ 工具执行（Reflexion 重试循环，现有逻辑不变）
  │
  └─→ after 中间件链（逆序执行）
        └─ OutputValidationGuard.after_execute(step, result, ctx)
              ├─ 返回值类型检查（dict）
              ├─ message 字段截断（≤ 10KB）
              ├─ data 字段大小检查（≤ max_output_size）
              └─ 敏感信息脱敏: api_key/sk-.../Bearer... → "***REDACTED***"
```

## 关键接口

### GuardMiddleware ABC（base.py）

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""
    modified_args: dict | None = None     # before: 可修改参数（如截断过长输入）
    modified_result: dict | None = None   # after: 可修改输出（如脱敏后替换）

class GuardMiddleware(ABC):
    @abstractmethod
    def before_execute(self, step: dict, ctx) -> GuardResult: ...
    
    @abstractmethod
    def after_execute(self, step: dict, result: StepResult, ctx) -> GuardResult: ...
```

### PermissionGuard（permission.py）

```python
class PermissionGuard(GuardMiddleware):
    def __init__(self, tool_registry, enable_admin_confirm=True,
                 write_require_confirm=False):
        self._registry = tool_registry
        self._enable_admin_confirm = enable_admin_confirm
        self._write_require_confirm = write_require_confirm
    
    def before_execute(self, step, ctx):
        spec = self._registry.get(step.get("tool", ""))
        if spec is None:
            return GuardResult(False, f"未知工具: {step.get('tool')}")
        perm = getattr(spec, 'permission', 'read')
        if perm == "read":
            return GuardResult(True)
        if perm == "write" and self._write_require_confirm:
            return GuardResult(False, "write_confirm_required")
        if perm == "admin":
            if self._enable_admin_confirm:
                return GuardResult(False, "admin_confirm_required")
            return GuardResult(True)
        return GuardResult(True)
```

### ExecutionEngine 中间件注入

```python
class ExecutionEngine(QObject):
    step_requires_confirmation = pyqtSignal(str, str, list)  # node_id, prompt, choices

    def __init__(self, ..., middlewares=None):
        ...
        self._middlewares: list[GuardMiddleware] = middlewares or []
        self._pending_confirmations: dict[str, GuardResult] = {}

    def _run_single(self, step):
        # before 链（任一返回 False → 停止）
        for mw in self._middlewares:
            result = mw.before_execute(step, self._ctx)
            if not result.allowed:
                if result.reason in ("admin_confirm_required", "write_confirm_required"):
                    # 发信号等待用户确认 → 后台线程 QEventLoop 等待
                    return self._wait_for_confirmation(step, result)
                return StepResult(success=False, message=f"护栏拒绝: {result.reason}")
        # ... 工具执行（现有逻辑）
        # after 链
        for mw in reversed(self._middlewares):
            result = mw.after_execute(step, raw_result, self._ctx)
            if not result.allowed:
                return StepResult(success=False, message=f"输出校验拒绝: {result.reason}")
    
    def provide_decision(self, node_id: str, choice: str):
        """UI 调用：用户确认结果。choice="continue" 继续，"skip" 跳过，"abort" 终止"""
        self._pending_confirmations[node_id] = choice
        # 退出 QEventLoop（由 HITL 逻辑处理，S10 实现）
```

## 实现步骤

### 步骤 1: GuardMiddleware ABC + GuardResult

**涉及文件**: `src/transbridge/smart_assistant/guardrails/base.py`（新建）

**实现要点**:
- GuardResult dataclass: allowed/reason/modified_args/modified_result
- GuardMiddleware ABC: before_execute(step, ctx) + after_execute(step, result, ctx)

**边界条件**: modified_args 非空时替换 step["args"]；modified_result 非空时替换 result 的对应字段

### 步骤 2: PermissionGuard

**涉及文件**: `src/transbridge/smart_assistant/guardrails/permission.py`（新建）

**实现要点**:
- 从 ToolSpec.permission 读取权限级别
- read → 直接放行
- write + require_confirmation → 触发确认（与 admin 同样流程）
- admin → 触发确认（除非全局禁用 enable_admin_confirm=False）
- 确认信号通过 ExecutionEngine.step_requires_confirmation 发射

**边界条件**: 工具未在 ToolRegistry 注册 → 拒绝；permission 字段缺失 → 默认 "read"（安全默认拒绝未知权限）

### 步骤 3: InputValidationGuard

**涉及文件**: `src/transbridge/smart_assistant/guardrails/input_validator.py`（新建）

**实现要点**:
- 参数类型校验：args 必须是 dict
- 字符串值长度检查：递归遍历 args，每个字符串 ≤ max_input_size
- 注入模式检测 regex：SQL 模式 (`'; DROP`/`UNION SELECT`)、XSS 模式 (`<script>`/`onerror=`)、命令注入模式 (`; rm`/`| cat`/`` `cmd` ``)

**边界条件**: 非字符串类型值跳过注入检测；嵌套 dict/list 递归遍历；检测到注入 → 拒绝并记录 reason

### 步骤 4: OutputValidationGuard

**涉及文件**: `src/transbridge/smart_assistant/guardrails/output_validator.py`（新建）

**实现要点**:
- 返回值类型校验：ToolSpec.execute 返回的 dict 必须含 success/message 字段
- 大小截断：message 截断至 10KB，data 字段 JSON 序列化后 ≤ max_output_size
- 敏感信息脱敏 regex：`sk-[a-zA-Z0-9]+`、`Bearer [a-zA-Z0-9._-]+` → `***REDACTED***`

**边界条件**: data 含不可序列化对象 → 跳过脱敏，写警告日志；message 为 None → 替换为空字符串

### 步骤 5: ToolSpec 扩展 + 现有工具 permission 标注

**涉及文件**: `src/transbridge/smart_assistant/tool_registry.py`（修改）

**实现要点**:
- ToolSpec 新增 3 个字段：`permission: str = "read"`, `require_confirmation: bool = False`, `max_output_size: int = 102400`
- v1 工具 permission 分配：lookup_terms/check_quality/get_collection_summary → "read"；translate_entries/export_json → "write"；write_back → "admin"
- 向后兼容：旧代码创建 ToolSpec 不传 permission → 默认 "read"

### 步骤 6: ExecutionEngine 注入 + 护栏配置

**涉及文件**: `src/transbridge/smart_assistant/execution_engine.py`（修改）、`src/transbridge/paratranz/config_manager.py`（修改）

**实现要点**:
- ExecutionEngine.__init__ 新增 `middlewares` 参数（可选，默认空列表）
- _run_single 注入 before 链 → 执行 → after 链
- step_requires_confirmation 信号定义
- [guardrails] INI section: enable_admin_confirm=true / enable_input_validation=true / enable_output_validation=true / max_input_size=102400 / write_require_confirm=false

### 步骤 7: ChatWidget 确认弹窗 + UI

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/chat_widget.py`（修改）

**实现要点**:
- 连接 step_requires_confirmation 信号 → QMessageBox.question() 弹窗
- 弹窗显示：工具名称 + 权限级别 + 操作描述 + "继续"/"跳过"/"终止" 按钮
- 用户选择后调用 executor.provide_decision(node_id, choice)

### 步骤 8: guardrails/__init__.py

**涉及文件**: `src/transbridge/smart_assistant/guardrails/__init__.py`（新建）

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/guardrails/__init__.py` | 新建 | 子包入口 |
| `smart_assistant/guardrails/base.py` | 新建 | GuardMiddleware ABC + GuardResult |
| `smart_assistant/guardrails/permission.py` | 新建 | PermissionGuard |
| `smart_assistant/guardrails/input_validator.py` | 新建 | InputValidationGuard |
| `smart_assistant/guardrails/output_validator.py` | 新建 | OutputValidationGuard |
| `smart_assistant/tool_registry.py` | 修改 | ToolSpec 新增 permission/require_confirmation/max_output_size；v1 工具分配 permission |
| `smart_assistant/execution_engine.py` | 修改 | 中间件链注入 + step_requires_confirmation 信号 + provide_decision |
| `ui/tools/smart_assistant/chat_widget.py` | 修改 | 确认弹窗 |
| `paratranz/config_manager.py` | 修改 | [guardrails] INI section |

## 风险与注意事项

- **风险**: 注入检测误报导致合法工具调用被拒 → 缓解：注入检测仅用简单正则（非语义分析），误报率低；可配置禁用
- **注意**: PermissionGuard 依赖 ToolSpec.permission 字段——必须在 ToolSpec 扩展（步骤5）完成后才能初始化。初始化顺序：ToolRegistry 注册 → PermissionGuard(registry) → ExecutionEngine(middlewares=[PermissionGuard])
- **注意**: after 中间件链逆序执行（洋葱模型），与 before 链对称
- **注意**: admin 确认信号依赖 S10 的 QEventLoop 暂停/恢复机制。S10 未完成时，admin 确认可降级为同步弹窗（在主线程中调用 confirm，后台线程等待）
