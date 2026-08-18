# ADR-011: 自研有状态图编排引擎

- **状态**: 已接受
- **日期**: 2026-05-10
- **决策者**: BuMing
- **对应需求**: FR7.13.7
- **关联 ADR**: [ADR-005](005-toml-prompt-no-langchain.md)（拒绝 LangChain/LangGraph）、[ADR-009](009-agent-file-memory-reflexion.md)（RetryHandler 注入模式）、[ADR-010](010-infra-extraction.md)（infra/ 基础设施）

## Context

FR7.13 Phase 2 需要将 smart_assistant 的执行模型从线性 DAG（steps 列表 + `depends_on` 拓扑排序）升级为有状态图编排引擎，支持以下四种新的执行语义：

- **条件分支**: 根据上一步结果动态决定下一步执行路径（如：翻译质量评分 < 0.7 则触发润色节点，否则跳过）
- **循环**: 子图重复执行直到满足退出条件（如：质量检查 → 不通过 → 重新翻译 → 再检查，最多 3 轮）
- **人机协同**: 执行过程中暂停，等待用户确认后继续（如：自动修复建议需用户审核）
- **断点恢复**: 异常中断或用户取消后，下次执行从 checkpoint 恢复而非从头开始

当前 `ExecutionEngine`（`src/transbridge/smart_assistant/execution_engine.py`，154 行）仅支持 DAG 拓扑排序 + 层级并行 + RetryHandler 注入，无法表达上述语义。需要决策：引入 LangGraph 还是自研扩展。

## Decision

### 1. 技术选型：自研轻量方案，零新依赖

不引入 LangGraph / LangChain，基于现有 `ExecutionEngine` 扩展。

**理由**:

| 维度 | LangGraph | 自研 StatefulDAGExecutor |
|------|-----------|-------------------------|
| PyQt6 QThread 集成 | 依赖 asyncio event loop，与 QThread 冲突（ADR-005 已论证） | 原生 threading + QEventLoop（仅人机协同时用 local loop） |
| 暂停/恢复控制流 | 需自定义 `interrupt` + `Command` 机制，学习曲线陡 | `threading.Event` + `QEventLoop`，与现有 cancel 机制一致 |
| 条件分支 | `add_conditional_edges` 声明式路由 | BFS 遍历 + `safe_eval(condition, graph_state)` |
| Checkpoint | 内置 SQLite/pickle，与项目 JSON 持久化风格不一致 | 自研 JSON Checkpoint，与项目 `current.json` / `memory_metadata.json` 风格统一 |
| 依赖体积 | 30+ 传递依赖 | 0 额外依赖 |
| 代码增量 | ~2000 行框架代码 | ~300 行增量（基于现有 154 行 ExecutionEngine） |
| 学习成本 | 需团队学习 LangGraph 概念（StateGraph, Node, Edge, Channel） | 团队已熟悉现有 ExecutionEngine 模式 |

项目已有明确的拒绝 LangChain/LangGraph 记录（ADR-005），且当前需求复杂度适合自研——四个新节点类型 + checkpoint 序列化，不需要完整的 agent 编排框架。

### 2. GraphExecutor 抽象基类（预留未来替换）

定义 `GraphExecutor` ABC 作为执行引擎的抽象契约。当前实现为 `StatefulDAGExecutor(GraphExecutor)`，未来如需切换 LangGraph 只需实现 `LangGraphExecutor(GraphExecutor)`，上层调用方无需修改。

```python
from abc import ABC, abstractmethod

class GraphExecutor(ABC):
    """图执行引擎抽象基类。定义 execute / cancel / pause / resume 四元组。"""

    @abstractmethod
    def execute_graph(self, graph: GraphSpec) -> list[StepResult]:
        """执行图并返回所有节点结果。"""
        ...

    @abstractmethod
    def cancel(self) -> None:
        """取消执行，保留 checkpoint 供下次恢复。"""
        ...

    @abstractmethod
    def pause(self) -> None:
        """暂停执行（在下一个节点执行前生效）。"""
        ...

    @abstractmethod
    def resume(self) -> None:
        """从暂停状态恢复。"""
        ...
```

**文件位置**: `src/transbridge/smart_assistant/graph_executor.py`

### 3. Node 类型体系

所有节点类型定义在 `src/transbridge/smart_assistant/graph_types.py`。

```python
from dataclasses import dataclass, field

@dataclass
class NodeSpec:
    """节点基类"""
    node_id: str
    node_type: str  # "action" | "condition" | "loop" | "human_confirm"

@dataclass
class ActionNode(NodeSpec):
    """工具执行节点：调用 ToolRegistry 中的工具，与现有 step 语义一致"""
    tool: str                    # 工具名称
    args: dict                   # 工具参数
    agent: str | None = None     # 执行 Agent（None 表示编排 Agent 默认）
    retry: bool = True           # 是否启用 Reflexion 重试

@dataclass
class ConditionNode(NodeSpec):
    """条件分支节点：根据表达式求值结果路由到不同下游"""
    condition: str               # 条件表达式，如 "result.data['score'] < 0.7"
    true_node: str               # 条件为 true 时的下游 node_id
    false_node: str              # 条件为 false 时的下游 node_id

@dataclass
class LoopNode(NodeSpec):
    """循环节点：重复执行子图直到满足退出条件或达到最大迭代次数"""
    sub_nodes: list[NodeSpec]    # 循环体内的节点列表
    max_iterations: int = 10     # 硬上限（防止死循环）
    exit_condition: str          # 退出条件表达式，如 "result.data.get('all_passed')"

@dataclass
class HumanConfirmNode(NodeSpec):
    """人机协同确认节点：暂停执行，等待用户选择后继续"""
    prompt: str                  # 向用户展示的确认提示
    choices: list[str]           # 可选项，如 ["继续", "跳过", "终止"]
    timeout_seconds: int = 300   # 超时秒数
    default_choice: str = "继续"  # 超时后的默认选择

@dataclass
class GraphSpec:
    """图定义：节点 + 边 + 入口"""
    graph_id: str
    nodes: list[NodeSpec]
    edges: list[dict]            # [{"from": "node1", "to": "node2", "type": "always"|"conditional"|"loop_back"}]
    entry_node: str              # 入口 node_id
```

**边类型说明**:

| edge.type | 含义 | 使用场景 |
|-----------|------|---------|
| `"always"` | 无条件跳转 | ActionNode → 下一个 ActionNode |
| `"conditional"` | 条件跳转 | ConditionNode 决策后路由 |
| `"loop_back"` | 循环回边 | LoopNode 尾部回到首部 |

### 4. Checkpoint 序列化契约

```python
from dataclasses import dataclass

@dataclass
class Checkpoint:
    graph_id: str
    current_node_id: str
    completed_results: dict[str, StepResult]   # node_id -> StepResult
    graph_state: dict                           # 可 JSON 序列化的状态字典
    timestamp: str                              # ISO format
```

**data 字段协议约束**: `StepResult.data` 仅允许 `dict | list | str | int | float | bool | None` 类型。Checkpoint 序列化前校验，不可序列化对象跳过并写警告日志。此约束在 `ActionNode` 的工具执行结果构建 `StepResult` 时由 `StatefulDAGExecutor` 校验，而非分散到各个工具实现。

**Checkpoint 存储路径**: `data/projects/{project}/{variant}/checkpoints/{graph_id}_{timestamp}.json`

与 ADR-009 的 memory 存储路径 `data/projects/{project}/{variant}/memory/` 同级，随项目/变体隔离。

**StatefulDAGExecutor 的 checkpoint 方法**:

```python
class StatefulDAGExecutor(GraphExecutor):
    def save_checkpoint(self) -> Checkpoint: ...
    def load_checkpoint(self, ckpt: Checkpoint) -> None: ...
    def resume_from_checkpoint(self, ckpt: Checkpoint) -> list[StepResult]: ...
```

**checkpoint 触发时机**:
- 每个节点执行完成后自动 `save_checkpoint()`（每层写入一次，而非每个节点——避免 IO 抖动）
- `cancel()` 调用时显式 `save_checkpoint()`
- 异常捕获后 `save_checkpoint()`，保留断点

### 5. 与现有系统的兼容

现有 `execute(steps: list[dict])` 接口**保持不动**，完全向后兼容。

**兼容策略**:
- 新增 `execute_graph(graph: GraphSpec)` 接口
- `execute()` 内部将 steps 转为线性 `GraphSpec`（全 `ActionNode`，`edges` 按 `depends_on` 生成，无分支/循环），委托给 `execute_graph()`
- 现有 `ChatWidget` 和 `PlanCard` 调用 `execute()` 的方式不变
- PyQt6 信号保持: `step_started / step_finished / all_finished / progress / step_retrying`

**新增信号**:
- `node_paused(node_id: str, prompt: str, choices: list[str])` — 遇到 HumanConfirmNode 时发出
- `node_resumed(node_id: str)` — 用户做出选择后发出

```python
from PyQt6.QtCore import pyqtSignal

class StatefulDAGExecutor(QObject):
    # 保留现有信号
    step_started = pyqtSignal(int, str)       # step_id, tool_name (兼容，内部映射 node_id 为 int)
    step_finished = pyqtSignal(StepResult)
    all_finished = pyqtSignal(list)            # list[StepResult]
    progress = pyqtSignal(int, int)            # completed, total
    step_retrying = pyqtSignal(int, int)       # step_id, attempt

    # 新增信号
    node_paused = pyqtSignal(str, str, list)   # node_id, prompt, choices
    node_resumed = pyqtSignal(str)             # node_id
```

**旧接口映射**（`execute()` 内部）:

```python
def execute(self, steps: list[dict]) -> list[StepResult]:
    graph = GraphSpec(
        graph_id=f"linear_{uuid4().hex[:8]}",
        nodes=[ActionNode(
            node_id=str(s["id"]), node_type="action",
            tool=s["tool"], args=s.get("args", {}), agent=s.get("agent"),
            retry=s.get("retry", True),
        ) for s in steps],
        edges=self._build_edges_from_depends_on(steps),
        entry_node=str(steps[0]["id"]),
    )
    return self.execute_graph(graph)
```

### 6. Graph 执行流程

```
StatefulDAGExecutor.execute_graph(graph):
  1. 检查是否有 checkpoint → 有则从 checkpoint 恢复 graph_state 和已完成结果
  2. 从 entry_node（或 checkpoint 中的 current_node_id）开始 BFS 遍历
  3. 同层 ActionNode 并行执行（复用现有 ThreadPoolExecutor + MAX_WORKERS=4）
  4. ActionNode → _run_single()（复用现有逻辑 + RetryHandler 注入）
  5. ConditionNode → safe_eval(condition, graph_state) → 路由到 true_node 或 false_node
  6. LoopNode → 执行 sub_nodes → 检查 exit_condition → 满足跳出 / 不满足继续（最多 max_iterations 轮）
  7. HumanConfirmNode → 发 node_paused 信号 → 启动 QEventLoop local loop 等待 → 用户选择后继续
  8. 每层完成后自动 save_checkpoint()
  9. 取消/异常 → 保留 checkpoint，下次调用时从断点恢复
```

**safe_eval 实现**: 条件表达式求值使用受限的 `eval()`，仅允许访问 `result` 和 `state` 两个变量 + 安全内置函数（`int, float, str, bool, len, max, min, abs, round, isinstance, dict.get, list`），禁止 `__` 和 import 语句。

### 7. 人机协同实现

执行线程为后台线程（非主线程），使用 `QEventLoop` local loop 等待用户确认。

**流程**:

```
1. 后台线程遇到 HumanConfirmNode
   → 发送 node_paused(node_id, prompt, choices) 信号
   → 创建 QEventLoop local loop（非主线程的嵌套事件循环）
   → 阻塞等待

2. UI 层（主线程）收到 node_paused 信号
   → 弹窗展示确认提示 + 选项按钮
   → 用户点击某选项 → 主线程调用 executor.provide_decision(node_id, choice)
   → 超时: QTimer 在 timeout_seconds 后自动调用 provide_decision(node_id, default_choice)

3. provide_decision(node_id, choice):
   → 将 choice 写入 graph_state["human_decisions"][node_id]
   → 退出 QEventLoop → 后台线程继续执行

4. 后续 ActionNode 可通过 args 中的 "{{human_decisions.node_id}}" 引用用户选择
```

**provide_decision 接口**:

```python
class StatefulDAGExecutor(GraphExecutor):
    def provide_decision(self, node_id: str, choice: str) -> None:
        """由主线程调用，提供用户对 HumanConfirmNode 的选择。线程安全。"""
        ...
```

**注意**: `provide_decision()` 必须在主线程调用（操作 `QEventLoop`），`node_paused` 信号通过 `Qt.QueuedConnection` 确保跨线程安全。

### 8. 文件清单

```
src/transbridge/smart_assistant/
├── graph_executor.py            # 新建：GraphExecutor ABC
├── graph_types.py               # 新建：NodeSpec / ActionNode / ConditionNode / LoopNode / HumanConfirmNode / GraphSpec / Checkpoint
└── execution_engine.py          # 改：ExecutionEngine → StatefulDAGExecutor(GraphExecutor)
```

**迁移计划**: `ExecutionEngine` 重命名为 `StatefulDAGExecutor`，原 `ExecutionEngine` 保留为兼容别名：

```python
# execution_engine.py 末尾
ExecutionEngine = StatefulDAGExecutor  # 向后兼容别名，deprecated
```

现有 import `from src.transbridge.smart_assistant.execution_engine import ExecutionEngine` 无需修改。

## 备选方案

| 方案 | 优点 | 缺点 |
|------|------|------|
| A: 引入 LangGraph | 内置 StateGraph / checkpoint / interrupt 机制；社区生态成熟；声明式图定义简洁 | asyncio event loop 与 PyQt6 QThread 冲突（ADR-005 已论证）；30+ 传递依赖增加打包体积；checkpoint 使用 SQLite/pickle 与项目 JSON 风格不一致；学习曲线陡峭；需大量适配代码绕过 asyncio |
| B: 自研轻量 StatefulDAGExecutor（**采用**） | 零新依赖；基于现有 154 行 ExecutionEngine 扩展，~300 行增量；与 QThread 原生兼容；JSON checkpoint 与项目持久化风格统一；团队已熟悉现有模式；GraphExecutor ABC 预留 LangGraph 切换路径 | 需自行实现 checkpoint 序列化 / 人机协同暂停恢复 / 循环控制；条件表达式 safe_eval 需防范注入风险；缺少可视化图编辑器（当前无此需求） |
| C: 继续用线性 DAG，不升级 | 零工作量；稳定性最高 | 无法支持条件分支、循环、人机协同——FR7.13 Phase 2 的核心需求无法实现 |

## Consequences

- **正面**:
  - 零新依赖，打包体积不变
  - 与现有 ExecutionEngine 完全向后兼容——`ChatWidget` / `PlanCard` 无需修改
  - GraphExecutor ABC 解耦了执行策略与调用方，未来可平滑切换到 LangGraph 或其他后端
  - JSON checkpoint 与项目 `current.json` / `memory_metadata.json` 风格一致，可被外部工具直接读取
  - 条件分支 + 循环为 Agent 自主决策提供基础能力（如：自动质量检查 → 不通过 → 重试循环）
  - 人机协同节点实现非阻塞式 UI 交互，用户可在长时间 Agent 任务中进行关键决策
  - 断点恢复能力避免长时间执行任务因意外中断而丢失进度

- **负面**:
  - 需自维护 checkpoint 序列化逻辑（JSON 序列化校验 / 反序列化重建）
  - 条件表达式 safe_eval 需要安全审查（限制内建函数白名单），避免代码注入风险
  - 循环节点内子图可能有嵌套 LoopNode → 需实现递归遍历，增加复杂度
  - 人机协同的 QEventLoop local loop 在非主线程使用需充分测试信号连接安全性

- **风险**:
  - safe_eval 实现不完善可能导致安全漏洞 → 使用 `eval()` 前严格校验表达式字符白名单 + AST 遍历检查，仅允许安全节点类型
  - QEventLoop 嵌套在非主线程的行为在不同 Qt 版本间可能存在差异 → 限制 PyQt6 >= 6.5，并在 CI 中增加人机协同场景的集成测试
  - 超时期间用户关闭应用 → checkpoint 已保存，下次启动检测到未完成的 checkpoint 提示用户是否恢复
  - 循环节点 max_iterations 默认 10 可能对某些场景不够 → 提供配置项，Agent 构建 GraphSpec 时可覆盖

### 更新：2026-08-18 — Graph 作为 Task Workload Adapter（已接受）

Graph/Node 语义继续保留，但执行实例必须作为 [ADR-019](019-unified-task-runtime.md) 的 Job 运行。graph checkpoint 成为统一 checkpoint envelope 的 payload，包含 owner、run_id、JobSpec/input digest、已计算/已提交边界和 idempotency key。GraphExecutor 不独立决定应用终态，也不得在取消后提交工具副作用；HumanConfirm 是可暂停 capability，没有交互通道的入口返回 blocked/failed 诊断而非创建 Qt local loop。
