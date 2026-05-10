# Story 07: UI 层 import 更新 + 验证

**所属方案**: `plans/llm-chat/plan.md`
**技术模块**: smart_assistant (UI)
**状态**: 已确认
**创建日期**: 2026-05-10

## 前置依赖

### 上游 Story
- Story-06（同 plan）：必须已完成 → 后端包 `src/transbridge/smart_assistant/` 就绪，6 个文件已搬迁，原 UI 目录下 6 个后端文件已删除

### 引用的架构决策
- [ADR-008: SmartAssistant 代码分层](../../../docs/adr/008-smart-assistant-code-layering.md) — 跨包使用绝对导入 `from src.transbridge.smart_assistant.xxx import ...`

## 验收标准

- [ ] `chat_widget.py` 3 处后端 import 改为绝对导入（指向 `src.transbridge.smart_assistant`）
- [ ] `plan_card.py` 1 处后端 import 改为绝对导入（`StepResult`）
- [ ] `main_window.py` 导入路径保持不变
- [ ] 启动应用无 ImportError
- [ ] 智能助手面板可正常打开和关闭
- [ ] LLM 对话功能正常（发送消息 → 收到回复）
- [ ] 6 个工具均可正常执行
- [ ] PlanCard 和 ToolCard 显示和交互正常

## 数据流

Story-06 搬迁后，UI 层对后端组件的相对导入全部失效（目标文件已不在同一目录）。本 Story 将这些导入全部替换为指向新包位置的绝对导入，恢复依赖链路：

```
chat_widget.py                          plan_card.py
    ├─→ .message_bubble (UI, 不变)         └─→ StepResult
    ├─→ .conversation_manager ❌ 失效           └─→ ❌ 失效
    ├─→ .chat_worker ❌ 失效
    ├─→ .execution_engine ❌ 失效
    ├─→ .tool_card (UI, 不变)
    └─→ .plan_card (UI, 不变)

                    ↓ Story-07 修复后

chat_widget.py                          plan_card.py
    ├─→ .message_bubble (UI, 不变)         └─→ smart_assistant.execution_engine ✅
    ├─→ smart_assistant.conversation_manager ✅
    ├─→ smart_assistant.chat_worker ✅
    ├─→ smart_assistant.execution_engine ✅
    ├─→ .tool_card (UI, 不变)
    └─→ .plan_card (UI, 不变)
```

## 实现步骤

### 步骤 1: 更新 `chat_widget.py` 3 处后端 import

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/chat_widget.py`（修改）

**修改内容**:

| 行 | 修改前 | 修改后 |
|----|--------|--------|
| 8 | `from .conversation_manager import ConversationManager` | `from src.transbridge.smart_assistant.conversation_manager import ConversationManager` |
| 9 | `from .chat_worker import ChatWorker` | `from src.transbridge.smart_assistant.chat_worker import ChatWorker` |
| 10 | `from .execution_engine import ExecutionEngine, StepResult` | `from src.transbridge.smart_assistant.execution_engine import ExecutionEngine, StepResult` |

**实现要点**:
- 仅替换 import 来源路径，不修改导入的符号名
- UI 内部导入（`.message_bubble`、`.tool_card`、`.plan_card`）保持不变
- `StepResult` 在 chat_widget.py 中也被使用（用于类型标注），继续从 `execution_engine` 导入

**边界条件**:
- 若 Story-06 未完成（后端包不存在）→ 此修改会导致 ImportError，步骤 3 验证会捕获

### 步骤 2: 更新 `plan_card.py` 1 处后端 import

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/plan_card.py`（修改）

**修改内容**:

| 行 | 修改前 | 修改后 |
|----|--------|--------|
| 6 | `from .execution_engine import StepResult` | `from src.transbridge.smart_assistant.execution_engine import StepResult` |

**实现要点**:
- `StepResult` 数据类用于类型标注（`on_step_finished(result: StepResult)`、`on_all_finished(results: list)`）
- 搬迁后 `StepResult` 随 `execution_engine.py` 移到新包，需更新导入路径

**边界条件**:
- 修改后 `StepResult` 的类接口不变 → 使用方代码无需其他改动

### 步骤 3: 启动验证 + 功能验证

**无代码修改**，逐项手动验证。

**实现要点**:

1. **Import 验证**（可命令行执行）:
   ```bash
   python -c "from src.transbridge.ui.tools.smart_assistant import SmartAssistantPanel; print('OK')"
   ```
   预期：输出 `OK`，无 ImportError 或 ModuleNotFoundError

2. **启动验证**：启动应用，检查：
   - 主窗口正常加载
   - 菜单「小工具→智能助手」可见
   - Ctrl+Shift+I 可打开/关闭面板
   - 无控制台异常输出

3. **功能验证**：
   - 发送消息 → LLM 正常响应
   - 点击「查询术语」快捷按钮 → 发送请求 → 工具执行
   - 点击「翻译选中」→ PlanCard 或 ToolCard 正常显示
   - 点击 PlanCard「执行计划」→ 步骤逐一执行
   - 点击 ToolCard「执行」→ 工具结果展示
   - 点击 ToolCard「忽略」→ 跳过正常

**边界条件**:
- API Key 未配置 → 应提示"请先配置 LLM API Key"而非报 ImportError
- 无集合加载 → 工具应返回"当前没有加载翻译集合"而非崩溃

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/tools/smart_assistant/chat_widget.py` | 修改 | 3 处 import 改为绝对导入 |
| `src/transbridge/ui/tools/smart_assistant/plan_card.py` | 修改 | 1 处 import 改为绝对导入 |

`main_window.py` 不在变更范围内，其现有 import `from src.transbridge.ui.tools.smart_assistant import SmartAssistantPanel` 保持不变。

## 风险与注意事项

- **风险**: 遗漏其他对后端文件的引用 → **缓解**: Grep 全项目搜索 `from .conversation_manager|from .chat_worker|from .execution_engine|from .tool_registry|from .context_builder|from .prompts` 确认无遗漏；UI 目录外无此类相对导入
- **注意**: 验证需在 Story-06 完成后进行，两个 Story 之间有严格顺序依赖——Story-06 先搬迁，Story-07 再修复 import
- **注意**: 验证需连接 LLM API，确保网络/VPN 可用；离线环境下仅验证 Import 层面（步骤 3.1）
