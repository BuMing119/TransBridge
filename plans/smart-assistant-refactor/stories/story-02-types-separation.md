# Story 02: base.py 类型定义分离

**所属方案**: `plans/smart-assistant-refactor/plan.md`
**技术模块**: backend
**状态**: 已实现
**创建日期**: 2026-05-22

## 前置依赖

### 上游 Story
- Story 01（ExecutionEngine 拆分）: 无直接依赖，但建议先完成 Story 01 以验证拆分流程

### 跨 Plan 依赖
- 无

### 引用的架构决策
- ADR-008 (2026-05-22 更新节): 类型定义独立为 types.py，原模块顶部重导出

## 验收标准

（从 plan 原样复制）

- [ ] `tools/types.py` 存在，包含 `ToolResult` / `ExecutionContext` / `HITLType` / `HITLRequest` / `HITLResponse`（~340行）
- [ ] `tools/base.py` 从 605行 缩减至 ≤300行，仅保留执行函数
- [ ] `base.py` 顶部 `from .types import ToolResult, ExecutionContext, HITLType, HITLRequest, HITLResponse` 重导出
- [ ] 所有工具模块的 `from .base import ToolResult, ExecutionContext` 继续可用
- [ ] `tools/__init__.py` 导出列表更新（如需要新增 types 模块导出）
- [ ] `ToolResult` 的 8 个方法行为不变
- [ ] `ExecutionContext` 的 `__getattr__` 代理逻辑不变
- [ ] 现有测试全部通过

## 数据流

```
外部 import:
  from .tools.base import ToolResult, ExecutionContext  ← 保持不变（base.py 重导出）

内部 import 关系:
  tools/types.py                    ← 类型定义（零依赖）
    ├── ToolResult
    ├── ExecutionContext
    ├── HITLType, HITLRequest, HITLResponse
    │
  tools/base.py                     ← 执行基础设施
    ├── from .types import ToolResult, ExecutionContext, HITLType, HITLRequest, HITLResponse
    ├── _build_guard_chain()
    ├── execute_with_guardrails()
    ├── filter_entries()
    ├── resolve_scope_to_entry_ids()
    ├── require_collection()
    └── validate_params()
    │
  tools/tool_*.py                   ← 工具模块
    └── from .base import ToolResult, ExecutionContext  ← 继续工作
```

## 关键接口

### tools/types.py（新建 ~340行）

从 base.py 中完整复制以下类，不改变任何代码：

```python
# tools/types.py — 工具系统类型定义

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection

@dataclass
class ToolResult:
    """工具执行结果（原样保留，不改代码）"""
    success: bool          # L40
    message: str           # L41
    data: Any = None       # L42
    # ... 其余字段 + 8 个方法

class ExecutionContext:
    """工具执行上下文（原样保留，不改代码）"""
    # ... 4 个方法

class HITLType(Enum):
    """人机交互类型"""
    CONFIRM = "confirm"
    SELECT = "select"
    INPUT = "input"

@dataclass
class HITLRequest:
    """人机交互请求"""
    # ...

@dataclass
class HITLResponse:
    """人机交互响应"""
    # ...
```

### tools/base.py（修改后 ~280行）

```python
# tools/base.py — 工具执行基础设施

# === 重导出（保持外部兼容） ===
from .types import (
    ToolResult,
    ExecutionContext,
    HITLType,
    HITLRequest,
    HITLResponse,
)
# 重新导入（本文件内部函数使用）
from .types import ToolResult, ExecutionContext

# === 执行函数（保留在本文件） ===
def _build_guard_chain() -> list | None: ...
def _apply_after_guards(...) -> ...: ...
def execute_with_guardrails(spec, args, ctx, ...) -> ToolResult: ...
def filter_entries(collection, ...) -> list: ...
def resolve_scope_to_entry_ids(ctx, collection) -> list[str] | None: ...
def require_collection(func) -> Callable: ...
def validate_params(schema) -> Callable: ...
```

## 实现步骤

### 步骤 1: 创建 tools/types.py

**涉及文件**: `src/transbridge/smart_assistant/tools/types.py`（新建）

**实现要点**:
- 从 `base.py` 第 30-361 行完整复制以下类（不改动一行代码）:
  - `ToolResult`（含所有字段 + 8 个方法: `__post_init__`, `ok`, `fail`, `partial`, `success`, `to_dict`, `to_json`, `format_for_llm`）
  - `ExecutionContext`（含 `__init__`, `get_collection`, `__repr__`, `__getattr__`）
  - `HITLType`（Enum）
  - `HITLRequest`（dataclass）
  - `HITLResponse`（dataclass）
- 只复制代码，复制后不改动
- 保留 `from __future__ import annotations` 和所有 import 语句
- 保留 TYPE_CHECKING 块

**边界条件**:
- `ExecutionContext.__getattr__` 代理访问 `AppContext` 属性 → 不改变代理行为
- `ToolResult.__post_init__` 的字段初始化逻辑 → 不改变
- TYPE_CHECKING 下的 import 仅在类型检查时生效 → 保留

**测试策略**:
- 无需新增测试（代码未改，仅移动文件位置）
- 运行现有测试确认 import 路径正常

### 步骤 2: 精简 tools/base.py

**涉及文件**: `src/transbridge/smart_assistant/tools/base.py`（修改）

**实现要点**:
- 删除已迁移到 `types.py` 的所有类定义（~330行）
- 在文件顶部添加重导出 import:
  ```python
  from .types import (
      ToolResult, ExecutionContext, HITLType, HITLRequest, HITLResponse,
  )
  ```
- 保留所有执行函数: `_build_guard_chain`, `_apply_after_guards`, `execute_with_guardrails`, `filter_entries`, `resolve_scope_to_entry_ids`, `require_collection`, `validate_params`, `_TYPE_MAP`
- 确保 `execute_with_guardrails` 等函数内部引用的 `ToolResult` 来自重导出（从 types 导入后在本文件可用）
- 文件应 ≤300行

**边界条件**:
- `from .base import ToolResult` 必须继续可用（通过 base.py 重导出实现）
- `from .base import ExecutionContext` 必须继续可用
- `from .base import HITLType, HITLRequest, HITLResponse` 必须继续可用
- 所有工具模块中的 `from .base import ToolResult, ExecutionContext` 无需修改

**测试策略**:
- 运行全量测试，确保所有工具 import 无 ImportError
- 检查关键 import 路径:
  - `tools/tool_default.py → from .base import ExecutionContext`
  - `tools/tool_translator.py → from .base import ToolResult`
  - `tools/tool_proofreader.py → from .base import ToolResult`
  - `tools/tool_paratranz.py → from .base import ToolResult`
  - `tools/tool_parser.py → from .base import ToolResult`
  - `tools/tool_editor.py → from .base import ToolResult`

### 步骤 3: 更新 tools/__init__.py

**涉及文件**: `src/transbridge/smart_assistant/tools/__init__.py`（修改）

**实现要点**:
- 在现有导出中添加 types 模块的公开符号（ToolResult, ExecutionContext, HITLType, HITLRequest, HITLResponse）
- 确保 `register_all()` 导入链不受影响

**测试策略**:
- `from src.transbridge.smart_assistant.tools import ToolResult` 正常工作
- `register_all()` 正常执行

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/smart_assistant/tools/types.py` | 新建 | 类型定义，~340行 |
| `src/transbridge/smart_assistant/tools/base.py` | 修改 | 删除类定义 + 加重导出，605→≤300行 |
| `src/transbridge/smart_assistant/tools/__init__.py` | 可能修改 | 新增 types 模块公开导出 |

## 风险与注意事项

- **风险 1**: 其他非 tools 目录的模块通过 `from .base import ToolResult` 导入 → 缓解：重导出保证兼容；全局搜索 `from .tools.base import` 和 `from .base import` 确认所有调用方
- **风险 2**: `ExecutionContext` 依赖 `AppContext`（通过 TYPE_CHECKING）→ 不改变依赖链，迁移后行为一致
- **注意 1**: 不改变 `ToolResult` 的 `__post_init__` 中 success 字段从 v1 三态到 v2 bool 的迁移逻辑
- **注意 2**: `ExecutionError` 等异常类如果存在也一并迁移到 types.py
