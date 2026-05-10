# Story 06: 新建后端包 + 文件搬迁

**所属方案**: `plans/llm-chat/plan.md`
**技术模块**: smart_assistant (backend)
**状态**: 已确认
**创建日期**: 2026-05-10

## 前置依赖

### 上游 Story
- Story-01~05（同 plan）：已完成 → smart_assistant 全部 13 个文件已存在

### 引用的架构决策
- [ADR-008: SmartAssistant 代码分层](../../../docs/adr/008-smart-assistant-code-layering.md) — 新建 `src/transbridge/smart_assistant/` 包，包内相对导入，跨包绝对导入

## 验收标准

- [ ] `src/transbridge/smart_assistant/` 包存在且含 `__init__.py`
- [ ] `__init__.py` 导出 8 个公开符号（ConversationManager, ChatWorker, ExecutionEngine, StepResult, ToolRegistry, ToolSpec, ContextBuilder, build_system_prompt）
- [ ] 6 个后端文件从 UI 目录搬迁到新包，原位置文件删除
- [ ] `prompts.py` 中 `from .tool_registry import ToolRegistry` 包内相对导入正确
- [ ] `from src.transbridge.smart_assistant import ConversationManager` 可成功执行

## 数据流

纯文件搬迁，无运行时数据流变化。搬迁后依赖方向：

```
UI 层 (chat_widget.py, plan_card.py)
    ↓ 绝对导入
smart_assistant/ 后端包
    ├── conversation_manager  (无内部依赖)
    ├── chat_worker           (无内部依赖)
    ├── execution_engine      (无内部依赖)
    ├── tool_registry         (无内部依赖)
    ├── context_builder       → AppContext (外部，绝对导入，不受搬迁影响)
    └── prompts               → .tool_registry (包内相对导入)
```

## 关键接口

### `__init__.py` 公开 API

```python
# src/transbridge/smart_assistant/__init__.py
from .conversation_manager import ConversationManager
from .chat_worker import ChatWorker
from .execution_engine import ExecutionEngine, StepResult
from .tool_registry import ToolRegistry, ToolSpec
from .context_builder import ContextBuilder
from .prompts import build_system_prompt

__all__ = [
    "ConversationManager",
    "ChatWorker",
    "ExecutionEngine",
    "StepResult",
    "ToolRegistry",
    "ToolSpec",
    "ContextBuilder",
    "build_system_prompt",
]
```

## 实现步骤

### 步骤 1: 创建后端包 `__init__.py`

**涉及文件**: `src/transbridge/smart_assistant/__init__.py`（新建）

**实现要点**:
- 创建 `src/transbridge/smart_assistant/` 目录
- 编写 `__init__.py`，导出 8 个公开符号
- 使用绝对导入（`from .xxx import ...`）引用同包模块

**边界条件**:
- 目录已存在 → 仅创建/覆盖 `__init__.py`，不影响已有文件
- `__init__.py` 导入模块不存在 → 搬迁完成前无法验证，步骤 2 后统一验证

**测试策略**:
- 搬迁完成后执行 `python -c "from src.transbridge.smart_assistant import ConversationManager, ChatWorker, ExecutionEngine, StepResult, ToolRegistry, ToolSpec, ContextBuilder, build_system_prompt"` → 无 ImportError

### 步骤 2: 搬迁 6 个后端文件

**涉及文件**: 6 文件搬迁 + 6 文件删除

| 源路径 (ui/tools/smart_assistant/) | 目标路径 (smart_assistant/) |
|------------------------------------|---------------------------|
| `conversation_manager.py` | `conversation_manager.py` |
| `chat_worker.py` | `chat_worker.py` |
| `execution_engine.py` | `execution_engine.py` |
| `tool_registry.py` | `tool_registry.py` |
| `context_builder.py` | `context_builder.py` |
| `prompts.py` | `prompts.py` |

**实现要点**:
- 使用文件系统操作（`mv` 或 Python `shutil.move`）将 6 个文件从 UI 目录移动到新包目录
- 移动后删除 UI 目录下的原文件
- 文件内容不做任何修改（步骤 3 单独处理）

**边界条件**:
- 目标文件已存在 → 覆盖（搬迁是幂等操作）
- 移动失败（权限/锁定）→ 报错终止，不继续删除源文件
- `context_builder.py` 有绝对导入 `from src.transbridge.ui.context import AppContext` → 绝对导入不受文件位置影响，搬迁后仍有效
- `tool_registry.py`、`chat_worker.py` 有外部绝对导入 → 同上，不受影响

**测试策略**:
- 搬迁后确认 UI 目录下 6 个文件已删除
- 确认新包目录下 6 个文件存在且内容完整（与搬迁前一致）

### 步骤 3: 更新 `prompts.py` 包内导入

**涉及文件**: `src/transbridge/smart_assistant/prompts.py`（修改）

**实现要点**:
- 当前 `prompts.py` 第 3 行：`from .tool_registry import ToolRegistry`
- 搬迁后 `prompts.py` 和 `tool_registry.py` 仍在同一包内 → 相对导入 `from .tool_registry import ToolRegistry` 不需要修改
- 但需验证导入正确性

**边界条件**:
- 如果之前是绝对导入 → 需改为相对导入（当前已是相对导入，无需变更）

**测试策略**:
- `python -c "from src.transbridge.smart_assistant.prompts import build_system_prompt; print(build_system_prompt()[:50])"` → 输出 system prompt 前 50 字符，无 ImportError

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/smart_assistant/__init__.py` | 新建 | 公开 API 导出 8 个符号 |
| `src/transbridge/smart_assistant/conversation_manager.py` | 新建（搬迁） | 从 ui/tools/ 移动 |
| `src/transbridge/smart_assistant/chat_worker.py` | 新建（搬迁） | 从 ui/tools/ 移动 |
| `src/transbridge/smart_assistant/execution_engine.py` | 新建（搬迁） | 从 ui/tools/ 移动 |
| `src/transbridge/smart_assistant/tool_registry.py` | 新建（搬迁） | 从 ui/tools/ 移动 |
| `src/transbridge/smart_assistant/context_builder.py` | 新建（搬迁） | 从 ui/tools/ 移动 |
| `src/transbridge/smart_assistant/prompts.py` | 新建（搬迁） | 从 ui/tools/ 移动 |
| `src/transbridge/ui/tools/smart_assistant/conversation_manager.py` | 删除 | 已搬迁 |
| `src/transbridge/ui/tools/smart_assistant/chat_worker.py` | 删除 | 已搬迁 |
| `src/transbridge/ui/tools/smart_assistant/execution_engine.py` | 删除 | 已搬迁 |
| `src/transbridge/ui/tools/smart_assistant/tool_registry.py` | 删除 | 已搬迁 |
| `src/transbridge/ui/tools/smart_assistant/context_builder.py` | 删除 | 已搬迁 |
| `src/transbridge/ui/tools/smart_assistant/prompts.py` | 删除 | 已搬迁 |

## 风险与注意事项

- **风险**: 搬迁后 UI 层 `chat_widget.py` 和 `plan_card.py` 的 import 路径失效 → **缓解**: Story-07 负责更新这些 import，Story-06 完成后 UI 层暂时不可用，需 Story-07 修复
- **注意**: `execution_engine.py` 搬迁后，`plan_card.py` 中的 `from .execution_engine import StepResult` 会失效（相对导入找不到模块）→ Story-07 改为绝对导入
- **注意**: 搬迁操作应在 Git 中保留文件历史 → 使用 `git mv` 而非普通文件移动，以保留 blame 历史
