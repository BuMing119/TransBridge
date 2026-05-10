# Story 07: Agent 编排与并行执行

**所属方案**: `plans/agent-upgrade/plan.md`
**技术模块**: smart_assistant/agents
**状态**: 已确认
**创建日期**: 2026-05-10

## 前置依赖

### 上游 Story
- Story-06（同 plan）：必须已完成 → AgentSpec/AgentRegistry/AgentInstance 数据类就绪 + ToolRegistry namespace 扩展就绪

### 引用的架构决策
- ADR-008 更新（2026-05-10）: Orchestrator 调度模型 + AgentWorker QThread 接口契约
- ADR-004: QThread + 信号总线异步模式

## 验收标准

- [ ] `Orchestrator` 类：decompose_task（LLM 分解用户请求→子任务列表）→ map_to_steps（子任务→ExecutionEngine step dict，含 agent 字段）→ summarize_results（汇总 StepResult 列表→用户可读摘要）
- [ ] `AgentWorker(QThread)` 类：接收 step + AgentInstance，在独立线程中执行工具调用，信号 `progress(str)`, `finished(StepResult)`, `error(str)`
- [ ] 同一类型 Agent 可创建多个 AgentWorker 实例（每个绑定不同项目）
- [ ] 多个 AgentWorker 通过 ExecutionEngine 的 ThreadPoolExecutor 并行调度
- [ ] 单个 Agent 失败不阻断其他 Agent（错误隔离）
- [ ] ChatWidget UI：Agent 状态指示器（空闲/执行中/完成/失败）

## 数据流

```
用户输入消息
  │
  ▼
Orchestrator.decompose_task(user_request, llm_client)
  │  LLM 分析请求 → 生成子任务列表
  │  [{"task_id": 1, "agent_type": "translator", "action": "翻译 Dragonborn 插件",
  │    "input_data": {"target": "Dragonborn.esp"}, "depends_on": []},
  │   {"task_id": 2, "agent_type": "proofreader", "action": "校对翻译结果",
  │    "input_data": {"source": "task_1_output"}, "depends_on": [1]}]
  ▼
Orchestrator.map_to_steps(subtasks, agent_registry)
  │  Subtask → ExecutionEngine step dict
  │  {"id": 1, "tool": "translate_entries", "agent": "translator",
  │   "agent_instance_id": "abc123", "args": {...}, "depends_on": []}
  ▼
ExecutionEngine.execute(steps)
  │  按 agent_instance_id 创建 AgentWorker 实例
  │  ThreadPoolExecutor 并行调度
  │  每个 AgentWorker 在自己的 QThread 中执行工具
  │  信号: progress → UI 状态更新
  │        finished(StepResult) → 汇总收集
  │        error → 错误隔离，不阻断其他 Agent
  ▼
Orchestrator.summarize_results(results, llm_client)
  │  LLM 汇总 → 用户可读摘要文本
  ▼
ChatWidget 显示结果 + Agent 状态指示器更新
```

## 关键接口

### orchestrator.py（新建）

```python
@dataclass
class Subtask:
    task_id: int
    agent_type: str           # "translator" | "proofreader" | "orchestrator"
    action: str                # 任务描述（一句话）
    input_data: dict           # 输入参数
    depends_on: list[int]      # 依赖的 task_id 列表


class Orchestrator:
    """编排 Agent：任务分解 → 调度映射 → 结果汇总。"""

    def __init__(self, agent_registry: AgentRegistry, tool_registry, llm_client):
        self._agents = agent_registry
        self._tools = tool_registry
        self._llm = llm_client

    def decompose_task(self, user_request: str, ctx) -> list[Subtask]:
        """调用 LLM 将用户请求分解为子任务列表。"""
        ...

    def map_to_steps(self, subtasks: list[Subtask]) -> list[dict]:
        """将子任务映射为 ExecutionEngine 的 step dict 列表。"""
        ...

    def summarize_results(self, results: list[StepResult], user_request: str) -> str:
        """汇总所有 Agent 的执行结果为用户可读摘要。"""
        ...
```

### agent_worker.py（新建）

```python
from PyQt6.QtCore import QThread, pyqtSignal

class AgentWorker(QThread):
    """单个 Agent 实例的执行线程。"""
    progress = pyqtSignal(str)          # 进度描述
    finished = pyqtSignal(StepResult)    # 执行完成
    error = pyqtSignal(str)              # 错误通知

    def __init__(self, step: dict, instance: AgentInstance,
                 tool_registry, parent=None):
        super().__init__(parent)
        self._step = step
        self._instance = instance
        self._tools = tool_registry
        self._cancelled = False

    def run(self):
        """QThread 主循环：查找工具 → 执行 → 发射信号。"""
        tool_name = self._step.get("tool", "")
        tool = self._tools.get(tool_name, namespace=self._instance.agent_spec.namespace)
        if tool is None:
            self.error.emit(f"工具不存在或无权访问: {tool_name}")
            return
        try:
            self.progress.emit(f"{self._instance.agent_spec.name} 正在执行 {tool_name}...")
            result = tool.execute(self._step.get("args", {}), self._instance.ctx)
            sr = StepResult(
                step_id=self._step["id"], tool=tool_name,
                success=result.get("success", True),
                message=result.get("message", ""),
                data=result.get("data"),
            )
            self.finished.emit(sr)
        except Exception as exc:
            self.error.emit(str(exc))

    def cancel(self):
        self._cancelled = True
```

## 实现步骤

### 步骤 1: 创建 Orchestrator

**涉及文件**: `src/transbridge/smart_assistant/agents/orchestrator.py`（新建）

**实现要点**:
- `decompose_task()`: 构建 prompt（含可用 Agent 列表 + 用户请求），调 LLM 生成子任务 JSON 列表，解析为 Subtask 列表
- `map_to_steps()`: 每个 Subtask → step dict：从 AgentRegistry 获取 AgentSpec，创建 AgentInstance（绑定 ctx 中的 project_path），将 Subtask.input_data 映射为 tool args，保留 depends_on
- `summarize_results()`: 构建 prompt（含原始请求 + 所有 StepResult），调 LLM 生成用户可读摘要
- LLM prompt 模板内联，不依赖外部 TOML 文件
- 职责边界：Orchestrator 只做调度和汇总，不执行工具。工具执行由 AgentWorker 完成

**边界条件**:
- LLM 返回非 JSON → 重试一次，仍失败则返回单任务（orchestrator 自己处理）
- Subtask 引用不存在的 agent_type → 跳过该 Subtask + 记录错误
- 空子任务列表 → 返回空列表，summarize_results 输出 "未找到合适的任务"

**测试策略**:
- mock LLM 返回固定 JSON，验证 decompose_task 解析正确
- 3 个 Subtask（1 翻译 + 1 校对 + 1 翻译）→ map_to_steps 生成正确 step dict
- summarize_results 接收混合成功/失败结果 → 输出含失败标记的摘要

### 步骤 2: 创建 AgentWorker(QThread)

**涉及文件**: `src/transbridge/smart_assistant/agents/agent_worker.py`（新建）

**实现要点**:
- 继承 QThread，遵循 ADR-004 异步模式
- run() 内：从 ToolRegistry 按 namespace 查找工具（利用 S06 的 namespace 扩展）
- 工具查找失败（namespace 限制）→ emit error 信号
- 执行成功 → emit finished(StepResult)
- 执行异常 → emit error(str(exc))
- cancel() 方法：设置 _cancelled 标志（当前不强制中断工具执行，仅标记）
- 职责边界：AgentWorker 是执行器，选择哪个工具由 step dict 决定，namespace 隔离由 ToolRegistry 保证

**边界条件**:
- AgentInstance 未设置 ctx → emit error
- ToolRegistry 中无对应工具 → emit error（权限不足或未注册）
- 工具执行抛出异常 → emit error，不崩溃

**测试策略**:
- AgentWorker 正常执行 → finished 信号携带正确 StepResult
- namespace 隔离：translator AgentWorker 尝试调用 proofreader 工具 → error 信号触发
- 多实例：创建 2 个 AgentWorker（同一 translator AgentSpec，不同 project_path）→ 各自独立执行

### 步骤 3: ExecutionEngine 适配多 Agent

**涉及文件**: `src/transbridge/smart_assistant/execution_engine.py`（修改）

**实现要点**:
- step dict 扩展可选字段 `agent_instance_id: str`
- `execute()` 中：当 step 有 `agent_instance_id` 时，查找对应的 AgentInstance，创建 AgentWorker 执行；当无此字段时（现有调用方），走原有 `_run_single()` 路径
- StepResult 新增可选字段 `agent_instance_id: str = ""` 用于追溯
- 职责边界：ExecutionEngine 的拓扑排序/层级并行逻辑不变，Agent 调度通过 step dict 注入

**边界条件**:
- step dict 无 agent 字段 → 完全向后兼容，走现有执行路径
- AgentInstance 不再存在（如被外部移除）→ 降级为 `_run_single()` 直接执行

### 步骤 4: ChatWidget UI 集成

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/chat_widget.py`（修改）

**实现要点**:
- 在输入区域上方添加 Agent 状态指示器（QHBoxLayout 含 3 个 QLabel：translator/proofreader/orchestrator）
- 状态显示：空闲（灰色圆点）、执行中（蓝色旋转）、完成（绿色）、失败（红色）
- `_on_send()` 时：创建 Orchestrator，调用 decompose_task → map_to_steps → ExecutionEngine.execute(steps)
- 接收 AgentWorker 信号 → 更新对应状态指示器
- 职责边界：UI 只管展示，Agent 创建和调度由 Orchestrator 处理

**边界条件**:
- 用户未选择 Agent → 默认使用 orchestrator（当前行为：直接调 LLM+工具）
- Agent 执行超时 → 状态显示为失败 + 停止按钮可中断

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/smart_assistant/agents/orchestrator.py` | 新建 | Orchestrator（decompose_task/map_to_steps/summarize_results） |
| `src/transbridge/smart_assistant/agents/agent_worker.py` | 新建 | AgentWorker(QThread) + progress/finished/error 信号 |
| `src/transbridge/smart_assistant/execution_engine.py` | 修改 | step dict 支持 agent_instance_id；StepResult 新增 agent_instance_id；多 AgentWorker 调度 |
| `src/transbridge/ui/tools/smart_assistant/chat_widget.py` | 修改 | Agent 状态指示器 + Orchestrator 集成 |

## 风险与注意事项

- **风险**: Orchestrator.decompose_task 依赖 LLM 质量，分解结果不稳定 → 缓解：prompt 中强制输出 JSON 格式 + 解析失败时单任务兜底
- **注意**: AgentWorker 使用 QThread 而非 ThreadPoolExecutor 的 future（因为需要信号）。多个 AgentWorker 并行时需注意 QThread 的 start/quit 生命周期管理
- **注意**: 同类型多实例（多项目）时，每个 AgentInstance 绑定不同 ctx（不同的 collection/esp_path），确保翻译结果隔离
