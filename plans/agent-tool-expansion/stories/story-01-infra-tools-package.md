# Story 01: 基础设施搭建 — tools/子包 + ToolResult + ExecutionContext + HITL + GuardChain + 装饰器

**所属方案**: `plans/agent-tool-expansion/plan.md`
**技术模块**: backend (smart_assistant)
**状态**: 已确认 (v2)
**创建日期**: 2026-05-11
**更新日期**: 2026-05-11（按修改确认书 v2 更新，范围显著扩大）

## 前置依赖

### 上游 Story
- 无（这是第一个 Story，后续所有 Story 的前置基础设施）

### 引用的架构决策
- ADR-008: smart_assistant 分层原则 + tools/ 子包归属业务逻辑层
- ADR-011: Checkpoint JSON 序列化要求（ToolResult.to_dict()）
- ADR-012: 安全护栏 read/write/admin 权限分级 + 中间件链（GuardChain）

## 范围说明 (v2 变更)

v2 版本将本 Story 从"基础搭建"升级为**工具系统核心基础设施**，新增以下职责：

| 编号 | 增项 | 来源 |
|------|------|------|
| B2 | ToolResult.get()/__getitem__ 字典兼容方法 | 兼容 execution_engine.py 的 `raw_result.get("success", True)` 旧调用 |
| B3 | success 从三态 `Literal[True, False, "partial"]` 改为 `bool` + 独立 `partial: bool` | 消除 `"partial"` truthy 字符串误判 bug |
| B4 | ExecutionContext 数据类（包装 AppContext + TaskManager） | 从 Story 13 提前，含 __getattr__ 代理 |
| H5 | HITLRequest/HITLResponse 数据类 | 统一 confirm/file_select/compare_confirm 三种人机交互 |
| B6 | execute_with_guardrails() 统一入口 | 消除 GUI/MCP 安全分叉，中间件链独立组件 |
| H8 | _filter_entries() 公共函数 | 供 Story 04/08/10 复用，消除重复实现 |
| H9 | ExecutionContext.__getattr__ 代理 | v1 工具零改动兼容新 ExecutionContext |
| E1 | 基础路径遍历检测 _detect_path_traversal() | InputValidationGuard 补全 ADR-012 设计但未实现的检测 |
| E5 | 装饰器堆叠顺序文档化 | @require_collection 最外层，@validate_params 内层 |
| E12 | 输出脱敏 list 递归处理 | OutputValidationGuard._redact_dict() 追加 list 递归 |

## 验收标准

- [ ] `smart_assistant/tools/` 子包存在，含 `__init__.py`、`base.py`、`tool_v1.py`
- [ ] `ToolResult` 数据类定义在 `base.py`：`success: bool`, `message: str`, `data: dict | None`, `failed_items: list | None`, `truncated: bool`, `partial: bool = False`（B3）
- [ ] `ToolResult` 添加 `get(key, default=None)` 和 `__getitem__` 字典兼容方法（B2）
- [ ] `ExecutionContext` 数据类定义在 `base.py`：`app_context` + `task_manager`，含 `__getattr__` 代理（B4 + H9）
- [ ] `HITLRequest`/`HITLResponse` 数据类定义在 `base.py`，覆盖 confirm/file_select/compare_confirm（H5）
- [ ] `execute_with_guardrails(spec, args, ctx)` 统一入口，中间件链：PermissionGuard → InputValidationGuard → 工具执行 → OutputValidationGuard（B6）
- [ ] `_filter_entries(collection, filter_state) -> list[TranslationEntry]` 公共函数（H8）
- [ ] `@require_collection` 装饰器可用；`@validate_params` 装饰器可用
- [ ] `base.py` 文档字符串中明确推荐装饰器顺序：`@require_collection` 最外层，`@validate_params` 内层（E5）
- [ ] InputValidationGuard 中实现 `_detect_path_traversal()` 基础版（检测 `../`、`..\\`、绝对路径）（E1）
- [ ] OutputValidationGuard 的 `_redact_dict()` 增加 list 类型递归处理（E12）
- [ ] 6 个 v1 工具函数迁移至 `tool_v1.py`，返回格式升级为 ToolResult
- [ ] `tool_registry.py` 仅保留 ToolSpec + ToolRegistry + v1 注册调用入口
- [ ] 现有所有调用方不受影响

## 关键接口 (v2)

### ToolResult 数据类

```python
# smart_assistant/tools/base.py

from dataclasses import dataclass, field
from typing import Any

@dataclass
class ToolResult:
    """工具执行结果。所有工具 SHALL 返回此类型。"""
    success: bool                    # B3: bool，不再使用 Literal[True, False, "partial"]
    message: str
    data: dict[str, Any] | None = None
    failed_items: list[dict[str, Any]] | None = None
    truncated: bool = False
    partial: bool = False            # B3: 独立字段，替代 success="partial"

    def to_dict(self) -> dict[str, Any]:
        """转为字典。success 保持为 bool，向后兼容。"""
        result = {"success": self.success, "message": self.message}
        if self.partial:
            result["partial"] = True
        if self.data is not None:
            result["data"] = self.data
        if self.failed_items is not None:
            result["failed_items"] = self.failed_items
        if self.truncated:
            result["truncated"] = self.truncated
        return result

    # B2: 字典兼容方法
    def get(self, key: str, default: Any = None) -> Any:
        """字典兼容：支持 execution_engine.py 的 raw_result.get("success", True) 调用。"""
        if key == "success":
            return self.success
        if key == "message":
            return self.message
        if key == "data":
            return self.data
        if key == "partial":
            return self.partial
        if key == "failed_items":
            return self.failed_items
        if key == "truncated":
            return self.truncated
        return default

    def __getitem__(self, key: str) -> Any:
        """字典兼容：支持 raw_result["success"] 调用。"""
        d = self.to_dict()
        return d[key]

    @classmethod
    def ok(cls, message: str = "操作成功", data: dict | None = None) -> "ToolResult":
        return cls(success=True, message=message, data=data)

    @classmethod
    def fail(cls, message: str, failed_items: list | None = None) -> "ToolResult":
        return cls(success=False, message=message, failed_items=failed_items)

    @classmethod
    def partial_ok(cls, message: str, data: dict | None = None,
                   failed_items: list | None = None) -> "ToolResult":
        """B3: 部分成功 —— success=True, partial=True"""
        return cls(success=True, partial=True, message=message, data=data, failed_items=failed_items)
```

### ExecutionContext (B4 + H9)

```python
@dataclass
class ExecutionContext:
    """工具执行上下文，包装 AppContext + TaskManager。
    
    __getattr__ 代理：未命中属性自动转发到内部 AppContext，
    使 v1 工具（接收裸 AppContext）零改动兼容新 ExecutionContext。
    """
    app_context: Any            # AppContext 实例
    task_manager: Any = None    # TaskManager 实例（Story 02 之前为 None）

    def __getattr__(self, name: str) -> Any:
        """H9: 未命中属性转发到 app_context。"""
        # 排除 dataclass 字段和私有属性，防止无限递归
        if name.startswith('_'):
            raise AttributeError(name)
        app_ctx = self.__dict__.get('app_context')
        if app_ctx is not None and hasattr(app_ctx, name):
            return getattr(app_ctx, name)
        raise AttributeError(
            f"'{type(self).__name__}' 和 'AppContext' 均无属性 '{name}'"
        )
```

### HITL 协议 (H5)

```python
from dataclasses import dataclass
from enum import Enum
from typing import Any

class HITLType(Enum):
    CONFIRM = "confirm"              # 确认弹窗
    FILE_SELECT = "file_select"      # parser 文件选择
    COMPARE_CONFIRM = "compare_confirm"  # 下载对比确认

@dataclass
class HITLRequest:
    """人机交互请求。"""
    type: HITLType
    title: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)  # 附加上下文
    timeout: int | None = None       # E11: 可配置超时，None=无限

@dataclass
class HITLResponse:
    """人机交互响应。"""
    approved: bool
    data: dict[str, Any] | None = None  # file_select 返回 {"path": "..."}
```

### execute_with_guardrails (B6)

```python
def execute_with_guardrails(spec: ToolSpec, args: dict, ctx: ExecutionContext) -> ToolResult:
    """统一工具执行入口，GUI 和 MCP 共享同一条中间件链。
    
    链: PermissionGuard → InputValidationGuard → spec.execute() → OutputValidationGuard
    """
    # 1. 权限检查
    perm_result = PermissionGuard.check(spec, args, ctx)
    if not perm_result.allowed:
        return ToolResult.fail(f"权限不足: {perm_result.reason}")
    if perm_result.requires_confirmation:
        hitl_req = HITLRequest(type=HITLType.CONFIRM, title="操作确认", 
                               message=perm_result.confirmation_message, context={"spec": spec, "args": args})
        # 由调用方（GUI/ExecutionEngine）处理 HITL 确认

    # 2. 输入校验
    valid_result = InputValidationGuard.validate(args, spec)
    if not valid_result.valid:
        return ToolResult.fail(f"输入校验失败: {valid_result.reason}")

    # 3. 执行
    result = spec.execute(args, ctx)

    # 4. 输出校验
    result = OutputValidationGuard.sanitize(result)

    return result
```

### _filter_entries (H8)

```python
def _filter_entries(collection: TranslationEntryCollection, 
                    filter_state: dict) -> list[TranslationEntry]:
    """公共筛选函数。根据 filter_state 从 collection 中筛选条目。
    
    供 Story 04/08/10 复用，统一筛选行为。
    """
    results = list(collection.entries())
    
    stages = filter_state.get("stage")
    if stages:
        results = [e for e in results if e.stage in stages]
    
    categories = filter_state.get("category")
    if categories:
        results = [e for e in results if e.context and any(c in e.context for c in categories)]
    
    labels = filter_state.get("label")
    if labels:
        # 标签筛选由调用方通过 ctx.entry_labels 配合完成
        pass
    
    search_query = filter_state.get("search_query")
    search_field = filter_state.get("search_field")
    if search_query:
        if search_field == "id":
            results = [e for e in results if search_query.lower() in e.id.lower()]
        elif search_field == "key":
            results = [e for e in results if search_query.lower() in (e.key or "").lower()]
        else:  # "text" or default
            results = [e for e in results if search_query.lower() in (e.original or "").lower()]
    
    return results
```

## 实现步骤

### 步骤 1: 创建 `smart_assistant/tools/` 子包

**涉及文件**: `src/transbridge/smart_assistant/tools/__init__.py`（新建）

- 创建 `tools/` 目录（与 `skills/`、`agents/` 等平级）
- `__init__.py` 导出核心类型：`ToolResult`, `ExecutionContext`, `HITLRequest`, `HITLResponse`

### 步骤 2: 定义 `ToolResult` 数据类（v2 版本）

**涉及文件**: `src/transbridge/smart_assistant/tools/base.py`（新建）

- `success: bool` + `partial: bool = False`（B3: 消除三态误判）
- `get(key, default)` + `__getitem__` 字典兼容（B2）
- `to_dict()` 保持向后兼容
- 工厂方法：`ok()`, `fail()`, `partial_ok()`

### 步骤 3: 定义 ExecutionContext (B4 + H9)

**涉及文件**: `src/transbridge/smart_assistant/tools/base.py`（追加）

- 包装 `app_context` + `task_manager`
- `__getattr__` 代理：未命中属性转发到 `app_context`（v1 工具零改动兼容）
- 注意排除 dataclass 字段和私有属性防止递归

### 步骤 4: 定义 HITL 协议 (H5)

**涉及文件**: `src/transbridge/smart_assistant/tools/base.py`（追加）

- `HITLType` 枚举：`confirm`, `file_select`, `compare_confirm`
- `HITLRequest` dataclass：type, title, message, context, timeout
- `HITLResponse` dataclass：approved, data

### 步骤 5: 实现 execute_with_guardrails() 统一入口 (B6)

**涉及文件**: `src/transbridge/smart_assistant/tools/base.py`（追加）

- PermissionGuard → InputValidationGuard → 工具执行 → OutputValidationGuard
- 提取为独立组件（非嵌入 ExecutionEngine），GUI 和 MCP 共享
- MCP 无 UI 通道时 admin/write 确认自动拒绝

### 步骤 6: 实现 _filter_entries() 公共函数 (H8)

**涉及文件**: `src/transbridge/smart_assistant/tools/base.py`（追加）

- 参数：`collection, filter_state` → 返回 `list[TranslationEntry]`
- 支持 stage/category/search_query/search_field 筛选
- 供 Story 04/08/10 复用

### 步骤 7: 实现装饰器

**涉及文件**: `src/transbridge/smart_assistant/tools/base.py`（追加）

- `@require_collection`：从 ctx 提取 collection
- `@validate_params(schema)`：按 ToolSpec.parameters 格式校验
- 文档明确推荐顺序：`@require_collection` 最外层，`@validate_params` 内层（E5）

### 步骤 8: 补全路径遍历检测 (E1)

**涉及文件**: `src/transbridge/smart_assistant/guardrails/input_validator.py`（修改）

- 实现 `_detect_path_traversal(args)` 方法
- 检测 `../`、`..\\`、绝对路径（Unix `/etc/`、Windows `C:\`）
- 基础版本：拒绝非项目目录内路径

### 步骤 9: 补全输出脱敏 list 递归 (E12)

**涉及文件**: `src/transbridge/smart_assistant/guardrails/output_validator.py`（修改）

- `_redact_dict()` 增加对 list 类型的递归处理
- 覆盖 `data.items: [str, str, ...]` 中嵌套在列表内的敏感信息

### 步骤 10: 迁移 v1 工具到 tool_v1.py

**涉及文件**: `tools/tool_v1.py`（新建）; `tool_registry.py`（修改）

- 从 `tool_registry.py` 复制 6 个函数
- 返回语句改为 `ToolResult.ok(...)` / `ToolResult.fail(...)` / `ToolResult.partial_ok(...)`
- `tool_registry.py` 中删除函数体，改为 `from .tools.tool_v1 import ...`

### 步骤 11: 更新导出

**涉及文件**: `smart_assistant/__init__.py`（修改）

- 新增导出：`ToolResult`, `ExecutionContext`, `HITLRequest`, `HITLResponse`

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/tools/__init__.py` | 新建 | 子包入口，导出核心类型 |
| `smart_assistant/tools/base.py` | 新建 | ToolResult(v2) + ExecutionContext + HITL + execute_with_guardrails + _filter_entries + 装饰器 |
| `smart_assistant/tools/tool_v1.py` | 新建 | 6 个 v1 工具函数（从 tool_registry.py 移入） |
| `smart_assistant/tool_registry.py` | 修改 | 删除 v1 工具函数体，改为从 tools.tool_v1 导入 |
| `smart_assistant/guardrails/input_validator.py` | 修改 | +_detect_path_traversal() |
| `smart_assistant/guardrails/output_validator.py` | 修改 | _redact_dict() 追加 list 递归 |
| `smart_assistant/__init__.py` | 修改 | 新增导出 |

## 风险与注意事项

- **风险 1**: Story 01 范围显著扩大（14 个步骤，7 个文件），工作量大 → 可拆分为 2-3 次对话完成，优先交付 ToolResult + ExecutionContext 以解锁并行 Story
- **风险 2**: ExecutionContext.__getattr__ 代理可能掩盖拼写错误 → 运行时 AttributeError 信息保留完整路径，logging 记录代理转发
- **风险 3**: execute_with_guardrails 与现有 ExecutionEngine._run_single() 功能重叠 → 本 Story 仅定义入口，Story 13 负责 ExecutionEngine 切换
- **注意**: `partial_ok()` 改为 `success=True, partial=True`，不再是 `success="partial"`，所有调用方 `if result.success` 不会误判
- **注意**: `@require_collection` 会改变函数签名（从 2 参数变 3 参数），仅用于需要 collection 的工具
