# Story 03: 循环控制与 UI 卡片

**所属方案**: `plans/llm-chat/plan.md`
**技术模块**: `src/transbridge/ui/tools/smart_assistant/` (新建)
**状态**: ✅ 已确认
**创建日期**: 2026-05-06

## 前置依赖

### 上游 Story
- Story-01: ChatWidget 基础框架 (消息列表、输入框、气泡方法、message_sent 信号)
- Story-02: ConversationManager (多轮对话), ChatWorker (LLM 流式调用), ExecutionEngine (步骤执行), parse_hybrid_response()

### 跨 Plan 依赖
- `ui-workbench/plan.md` → `AppContext` 状态访问 (只读)
- `ai-translation/plan.md` → `AutoTranslator`, `PostProcessor` (由 ToolRegistry 间接调用)

### 引用的架构决策
- [ADR-004: QThread + 信号总线](/docs/adr/004-qthread-async-pattern.md) — ExecutionEngine 在线程中执行，结果通过信号返回主线程

## 验收标准

- [ ] 用户发送消息后，ChatWidget 调用 LLM 并显示响应
- [ ] LLM 返回 mode=plan 时显示 PlanCard（步骤列表 + 依赖 + 执行/取消按钮）
- [ ] 用户确认 PlanCard 后 ExecutionEngine 按依赖顺序执行，每步显示进度
- [ ] 全部执行完成后聚合结果反馈给 LLM，LLM 输出最终总结
- [ ] LLM 返回 mode=react 且单步时显示 ToolCard（执行/忽略按钮）
- [ ] LLM 返回 mode=react 且多步时显示 BatchToolCard（全部执行按钮）
- [ ] 工具执行完成后结果反馈给 LLM 继续推理（ReAct 循环）
- [ ] 用户可点击"忽略"跳过工具调用，LLM 能继续推理
- [ ] ReAct 循环最大深度 10 轮，超出自动终止
- [ ] 计划执行中某步失败，不阻塞后续无依赖步骤

## 数据流

```
ChatWidget.message_sent
    │
    ▼
_run_llm_round()
    ├─ ConversationManager.get_messages()
    ├─ ChatWorker(messages) → chunk → 流式显示
    └─ ChatWorker.finished → _on_llm_finished(response)
         │
         ├─ thought 显示为 AI 气泡
         ├─ conversation.add_assistant(response)
         │
         └─ mode 分发:
              │
              ├─ mode="plan"
              │    └─ _add_plan_card(steps)
              │         │  用户 [执行计划]
              │         ▼  _on_plan_confirmed(steps)
              │              ExecutionEngine.execute(steps)
              │              ├─ step_started → PlanCard 高亮当前步骤
              │              ├─ step_finished → PlanCard 标记完成
              │              ├─ progress → PlanCard 更新进度条
              │              └─ all_finished → _on_plan_all_finished(results)
              │                   ├─ 构建 summary (✅/❌ × N)
              │                   ├─ conversation.add_plan_result(summary)
              │                   └─ _run_llm_round() → LLM 总结
              │
              ├─ mode="react", |steps|=1
              │    └─ _add_tool_card(steps[0])
              │         ├─ [执行] → _on_tool_executed
              │         │    ├─ ToolRegistry.execute() (Story-04)
              │         │    ├─ _add_system_message(✅/❌ result)
              │         │    ├─ conversation.add_observation(result)
              │         │    └─ _run_llm_round() → 继续循环
              │         └─ [忽略] → _on_tool_ignored
              │              ├─ _add_system_message("已忽略")
              │              ├─ conversation.add_observation("用户选择不执行")
              │              └─ _run_llm_round() → 继续循环
              │
              ├─ mode="react", |steps|>1
              │    └─ _add_batch_tool_card(steps)
              │         └─ [全部执行] → 逐个执行 → 聚合结果 → LLM
              │
              └─ mode="react", |steps|=0 → 纯文本回复，任务完成
```

## 关键接口

### chat_widget.py (扩展)

```python
class ChatWidget(QWidget):
    # 新增属性和信号
    message_sent = pyqtSignal(str)
    _react_depth: int = 0           # ReAct 循环计数器
    _MAX_REACT_DEPTH: int = 10

    # 新增方法
    def _run_llm_round(self) -> None:
        """创建 ChatWorker → 连接信号 → start() → 流式显示"""

    def _on_llm_finished(self, response: str) -> None:
        """parse_hybrid_response → 显示 thought → 模式分发 → 添加卡片/纯文本"""

    def _on_plan_confirmed(self, steps: list[dict]) -> None:
        """创建 ExecutionEngine → 连接信号 → 在后台线程执行"""

    def _on_plan_progress(self, completed: int, total: int) -> None:
        """更新 PlanCard 进度条"""

    def _on_plan_all_finished(self, results: list[StepResult]) -> None:
        """构建聚合 summary → conversation.add_plan_result → _run_llm_round()"""

    def _on_tool_executed(self, step: dict, result: dict) -> None:
        """显示结果 → conversation.add_observation → _run_llm_round()（继续循环）"""

    def _on_tool_ignored(self, step: dict) -> None:
        """显示"已忽略" → conversation.add_observation → _run_llm_round()"""

    def _add_tool_card(self, step: dict) -> ToolCard:
        """创建 ToolCard 添加到消息列表"""

    def _add_batch_tool_card(self, steps: list[dict]) -> BatchToolCard:
        """创建 BatchToolCard 添加到消息列表"""

    def _add_plan_card(self, steps: list[dict]) -> PlanCard:
        """创建 PlanCard 添加到消息列表"""

    def _check_react_depth(self) -> bool:
        """检查循环深度，超限时 _add_system_message("已达最大推理深度") → return False"""
```

### tool_card.py

```python
class ToolCard(QWidget):
    """单步工具确认卡片：黄色背景"""

    executed = pyqtSignal(dict)    # 用户点击"执行"
    ignored = pyqtSignal(dict)     # 用户点击"忽略"

    def __init__(self, step: dict, parent=None):
        """显示工具名 + 参数表格 + [执行] [忽略] 按钮"""

    def set_executing(self) -> None:
        """执行中状态：按钮 disabled，显示转圈"""

    def set_result(self, success: bool, message: str) -> None:
        """显示执行结果：✅/❌ + 消息"""

class BatchToolCard(QWidget):
    """多步工具确认卡片：黄色背景，步骤概览"""

    all_executed = pyqtSignal(list)   # 用户点击"全部执行"

    def __init__(self, steps: list[dict], parent=None):
        """显示步骤概览列表 + [全部执行] 按钮"""
```

### plan_card.py

```python
class PlanCard(QWidget):
    """计划确认卡片：蓝色背景"""

    confirmed = pyqtSignal(list)     # 用户点击"执行计划"
    cancelled = pyqtSignal()         # 用户点击"取消"

    def __init__(self, steps: list[dict], parent=None):
        """步骤列表（含依赖标注）+ [执行计划] [取消] 按钮"""

    def on_step_started(self, step_id: int, tool_name: str) -> None:
        """高亮当前步骤，显示"执行中..." """

    def on_step_finished(self, result: StepResult) -> None:
        """标记步骤完成 ✅/❌"""

    def on_progress(self, completed: int, total: int) -> None:
        """更新进度条/文字"""

    def on_all_finished(self, results: list) -> None:
        """全部完成，卡片变为完成状态"""
```

## 实现步骤

### 步骤 1: 创建 ToolCard / BatchToolCard

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/tool_card.py`（新建）

**实现要点**:
- ToolCard: 黄色背景 (`#FFF8E1`)，显示 `tool_name` + `args` 键值对 + 两个按钮
- BatchToolCard: 同色系，显示 N 个步骤的名称列表 + [全部执行] 按钮
- 执行后按钮 disabled，显示执行结果

**边界条件**:
- args 为空字典 → 不显示参数行
- args 值含嵌套对象 → JSON.stringify 展示
- 按钮点击后立即 disabled → 防止重复执行
- 卡片在消息列表中正确渲染（高度自适应）

**测试策略**:
- 手动验证：ToolCard 显示工具名和参数
- 手动验证：点击"执行"→按钮 disabled→显示结果
- 手动验证：点击"忽略"→卡片消失→AI 继续

### 步骤 2: 创建 PlanCard

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/plan_card.py`（新建）

**实现要点**:
- 蓝色背景 (`#E3F2FD`)，步骤列表使用 QListWidget
- 每个步骤项显示: `步骤 {id}: {tool}` + 依赖标注
- 进度预览: `QProgressBar` 或文字 `(0/N)`
- 执行中实时更新步骤状态图标

**边界条件**:
- 取消后 ExecutionEngine 仍在执行 → cancel() 优雅中断，已完成的保留
- PlanCard 被 remove 前 → 断开 ExecutionEngine 信号连接
- 步骤数为 0 → 不显示此卡片（逻辑上 parse_hybrid_response 后不会产生 mode=plan + steps=[]）

**测试策略**:
- 手动验证：PlanCard 显示步骤+依赖+进度条
- 手动验证：点击执行→进度条更新→完成后 LLM 总结
- 手动验证：点击取消→ExecutionEngine 中断

### 步骤 3: 实现 ChatWidget 混合循环控制

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/chat_widget.py`（修改扩展）

**实现要点**:
- 整合 Story-01 的 ChatWidget 基础 + Story-02 的三个后端组件
- `_on_llm_finished()` 先显示 thought，再按 mode 分发
- `_run_llm_round()` 每次递增 `_react_depth`
- `_check_react_depth()` 超限后添加系统消息终止

**边界条件**:
- LLM 返回既非 plan 也非 react → 按纯文本处理
- ChatWorker error → 显示红色系统消息，不阻塞后续交互
- 面板关闭时 ChatWorker 仍在运行 → cancel + wait(3000)
- ReAct 深度限 10 轮 → 超限后显示"已达最大推理深度"

**伪代码**:
```python
def _on_llm_finished(self, response):
    parsed = self._prompt_builder.parse_hybrid_response(response)
    self.add_assistant_bubble(parsed["thought"])
    self._conversation.add_assistant(response)

    steps = parsed.get("steps", [])
    if parsed["mode"] == "plan" and steps:
        self._add_plan_card(steps)
    elif parsed["mode"] == "react" and len(steps) == 1:
        self._add_tool_card(steps[0])
    elif parsed["mode"] == "react" and len(steps) > 1:
        self._add_batch_tool_card(steps)
    # else: 纯文本，结束

def _on_tool_executed(self, step, result):
    tool_name = step["tool"]
    msg = f"✅ {tool_name}: {result.get('message', '完成')}" if result.get("success") \
     else f"❌ {tool_name}: {result.get('error', '失败')}"
    self._add_system_message(msg)
    if not self._check_react_depth():
        return
    self._conversation.add_observation(tool_name, msg)
    self._run_llm_round()
```

**测试策略**:
- 集成测试：发送"翻译 dragon 并检查质量" → PlanCard → 执行 → LLM 总结
- 集成测试：发送"检查质量" → ToolCard(collection_summary) → 执行 → ToolCard(check_quality) → 执行 → 完成
- 手动验证：连续点"忽略"10 次 → 循环正常继续
- 手动验证：发送"你好" → 纯文本回复，无卡片

### 步骤 4: 创建 __init__.py 导出

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/__init__.py`（新建/修改）

**实现要点**:
- 导出 `SmartAssistantPanel` 供 MainWindow 导入

**测试策略**:
- Import 验证：`from src.transbridge.ui.tools.smart_assistant import SmartAssistantPanel` 正确导入

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/tools/smart_assistant/tool_card.py` | 新建 | ToolCard + BatchToolCard |
| `src/transbridge/ui/tools/smart_assistant/plan_card.py` | 新建 | PlanCard |
| `src/transbridge/ui/tools/smart_assistant/chat_widget.py` | 修改 | 扩展循环控制（_run_llm_round, _on_llm_finished, 模式分发, ReAct 深度限制） |
| `src/transbridge/ui/tools/smart_assistant/__init__.py` | 修改 | 导出 SmartAssistantPanel |

## 风险与注意事项

- **ReAct 无限循环**: LLM 可能持续返回 tool_calls → `_MAX_REACT_DEPTH=10` 硬限制
- **ExecutionEngine 线程安全**: 所有 UI 更新（StepResult → 卡片状态）通过信号在主线程执行
- **PlanCard 取消后的清理**: cancel() 后 ExecutionEngine 仍在执行中，需等当前层级完成 → PlanCard 标记"已取消"
- **ChatWidget 代码膨胀**: 循环控制逻辑集中在 chat_widget.py → 考虑后续 Story-05 抽取 `LoopController` 类
