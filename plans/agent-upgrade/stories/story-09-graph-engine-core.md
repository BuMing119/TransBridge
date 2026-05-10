# Story 09: Graph 引擎核心

**所属方案**: `plans/agent-upgrade/plan.md`
**技术模块**: smart_assistant
**状态**: 已确认
**创建日期**: 2026-05-10

## 前置依赖

### 上游 Story
- Story-05（同 plan）：已完成 → ExecutionEngine 已有 DAG 拓扑排序 + _run_single + RetryHandler 注入
- Story-07（同 plan）：必须已完成 → step dict 已支持 agent 字段（Graph 引擎复用 agent 路由）

### 引用的架构决策
- ADR-011: 自研 Graph 引擎（StatefulDAGExecutor + GraphExecutor ABC + 4 Node 类型）

## 验收标准

- [ ] `GraphExecutor` ABC：`execute_graph(graph) → list[StepResult]` / `cancel()` / `pause()` / `resume()`
- [ ] `GraphSpec` 数据类（graph_id/nodes/edges/entry_node）
- [ ] `NodeSpec` 基类 + 4 种子类：ActionNode / ConditionNode / LoopNode / HumanConfirmNode
- [ ] `EdgeSpec`：from/to/type（"always"/"conditional"/"loop_back"）
- [ ] `StatefulDAGExecutor(GraphExecutor)`：BFS 遍历 + 同层并行 + 条件路由 + 循环控制
- [ ] 循环支持嵌套条件分支；不支持嵌套循环（Phase 2 限制）
- [ ] 条件表达式引擎：基于 StepResult 字段评估
- [ ] `execute()` 向后兼容：内部将 steps 转为线性 GraphSpec 委托给 `execute_graph()`
- [ ] ExecutionEngine 保留为别名
- [ ] PyQt6 信号保留现有 5 个 + 新增 `node_paused` / `node_resumed`

## 数据流

```
execute_graph(graph: GraphSpec)
  │
  ├─→ 拓扑排序（解析 edges + 各 Node 内部的隐式边）
  │      ActionNode → 按 edges 中的 to
  │      ConditionNode → 运行时根据结果选择 true_node / false_node
  │      LoopNode → 内部 sub_nodes + 回到起点的隐式 loop_back 边
  │      HumanConfirmNode → 暂停等待确认后继续
  │
  ├─→ BFS 层级遍历
  │      Level 0: [entry_node]
  │      Level 1: entry_node 的所有后继
  │      ...
  │      同层节点通过 ThreadPoolExecutor 并行执行
  │
  ├─→ 节点执行（dispatch）
  │      ActionNode → _run_single(step_dict)（复用现有逻辑）
  │      ConditionNode → 评估条件表达式 → 路由
  │      LoopNode → for i in range(max_iterations): 执行 sub_nodes → 检查 exit_condition
  │      HumanConfirmNode → 发射 node_paused 信号 → 等待 → 继续
  │
  └─→ 收集所有 StepResult → 返回
```

## 关键接口

### graph_types.py（新建）

```python
@dataclass
class NodeSpec:
    node_id: str
    node_type: str       # "action" | "condition" | "loop" | "human_confirm"

@dataclass
class ActionNode(NodeSpec):
    tool: str
    args: dict = field(default_factory=dict)
    agent: str | None = None
    retry: bool = True

@dataclass
class ConditionNode(NodeSpec):
    condition: str       # "result.data['score'] < 0.7"
    true_node: str
    false_node: str

@dataclass
class LoopNode(NodeSpec):
    sub_nodes: list[NodeSpec]
    max_iterations: int = 10
    exit_condition: str  # "result.data.get('all_passed')"

@dataclass
class HumanConfirmNode(NodeSpec):
    prompt: str
    choices: list[str] = field(default_factory=lambda: ["继续", "跳过", "终止"])
    timeout_seconds: int = 300
    default_choice: str = "继续"

@dataclass
class EdgeSpec:
    from_node: str
    to_node: str
    edge_type: str = "always"  # "always" | "conditional" | "loop_back"

@dataclass
class GraphSpec:
    graph_id: str
    nodes: list[NodeSpec]
    edges: list[EdgeSpec]
    entry_node: str
```

### graph_executor.py（新建）

```python
from abc import ABC, abstractmethod

class GraphExecutor(ABC):
    @abstractmethod
    def execute_graph(self, graph: GraphSpec) -> list[StepResult]: ...
    @abstractmethod
    def cancel(self) -> None: ...
    @abstractmethod
    def pause(self) -> None: ...
    @abstractmethod
    def resume(self) -> None: ...
```

### StatefulDAGExecutor 条件表达式引擎

```python
import re

def _eval_condition(condition: str, result: StepResult) -> bool:
    """安全的条件表达式求值。支持 result.success, result.data['key'] 等简单访问。"""
    if not condition.strip():
        return False
    # 支持模式: "True", "False", "result.success", "result.data['key'] == value"
    # 使用受限的 eval（仅允许 result 变量 + 安全内置函数）
    safe_globals = {"__builtins__": None, "result": result, "True": True, "False": False}
    try:
        return bool(eval(str(condition), safe_globals))
    except Exception:
        return False
```

## 实现步骤

### 步骤 1: Graph 类型体系

**涉及文件**: `src/transbridge/smart_assistant/graph_types.py`（新建）

**实现要点**:
- 定义 NodeSpec 基类 + 4 子类（Action/Condition/Loop/HumanConfirm）
- 定义 EdgeSpec、GraphSpec
- 所有类为 dataclass，支持 JSON 序列化（graph_id/node_id 等纯数据字段）
- node_type 字段使用字符串区分（避免 isinstance 开销）

**边界条件**: ActionNode 不含 tool 字段 → 执行时检查并报错

### 步骤 2: GraphExecutor ABC

**涉及文件**: `src/transbridge/smart_assistant/graph_executor.py`（新建）

**实现要点**:
- 4 元抽象接口：execute_graph / cancel / pause / resume
- 注释说明每个方法的语义和调用时机
- 预留 LangGraph 切换路径（未来实现 LangGraphExecutor(GraphExecutor)）

### 步骤 3: StatefulDAGExecutor 核心实现

**涉及文件**: `src/transbridge/smart_assistant/execution_engine.py`（修改——重写 execute 流程，保留 _run_single/_topological_levels）

**实现要点**:
- 新增 `execute_graph(graph: GraphSpec)` 方法（核心入口）
- BFS 层级遍历：从 entry_node 开始，按 edges 构建层级结构
- 同层并行：ThreadPoolExecutor（复用现有 _MAX_WORKERS=4）
- 节点 dispatch：根据 node_type 分发到不同执行路径
- ActionNode → _run_single（复用现有的 step dict 执行 + RetryHandler）
- ConditionNode → 评估条件表达式 → 在运行时决定路由（动态边）
- LoopNode → for 循环执行 sub_nodes → 每轮后检查 exit_condition
- HumanConfirmNode → placeholder（S10 实现完整逻辑，此处先 skip 并记录日志）
- 条件表达式引擎：受限 eval 或简单字符串匹配（支持 `result.success`/`result.data['key']` 访问）

**边界条件**:
- 图有环 → 循环检测（复用 _topological_levels 的环检测），返回错误
- entry_node 不存在 → 返回空列表 + 日志错误
- ConditionNode 条件表达式中引用不存在的 data key → _eval_condition 返回 False
- LoopNode max_iterations 为 0 → 不执行 sub_nodes

### 步骤 4: 向后兼容适配

**涉及文件**: `src/transbridge/smart_assistant/execution_engine.py`（修改）

**实现要点**:
- `execute(steps)` 内部：将 steps 转为线性 GraphSpec（全 ActionNode + always edges）→ 委托 execute_graph
- ExecutionEngine 保留为 StatefulDAGExecutor 的别名

**伪代码**:
```python
class StatefulDAGExecutor(GraphExecutor, QObject):
    def execute(self, steps: list[dict]) -> list[StepResult]:
        """向后兼容接口：将 step 列表转为线性 GraphSpec。"""
        nodes = []
        edges = []
        for i, s in enumerate(steps):
            node_id = f"step_{s['id']}"
            nodes.append(ActionNode(
                node_id=node_id, node_type="action",
                tool=s.get("tool", "?"), args=s.get("args", {}),
                agent=s.get("agent"), retry=s.get("retry", True),
            ))
            if i > 0:
                edges.append(EdgeSpec(from_node=f"step_{steps[i-1]['id']}", to_node=node_id))
        graph = GraphSpec(
            graph_id=f"linear_{id(steps)}", nodes=nodes, edges=edges,
            entry_node=f"step_{steps[0]['id']}" if nodes else "",
        )
        return self.execute_graph(graph)

ExecutionEngine = StatefulDAGExecutor  # 别名
```

### 步骤 5: 信号扩展

**涉及文件**: `src/transbridge/smart_assistant/execution_engine.py`（修改）

**实现要点**:
- 新增 `node_paused = pyqtSignal(str, str, list)` — node_id, prompt, choices
- 新增 `node_resumed = pyqtSignal(str)` — node_id
- 保留现有 5 个信号：step_started/step_finished/all_finished/progress/step_retrying

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/graph_types.py` | 新建 | 全部 Graph 类型定义（NodeSpec 族 + EdgeSpec + GraphSpec） |
| `smart_assistant/graph_executor.py` | 新建 | GraphExecutor ABC |
| `smart_assistant/execution_engine.py` | 修改 | StatefulDAGExecutor 实现 + execute() 兼容适配 + 信号扩展 |

## 风险与注意事项

- **风险**: 条件表达式 eval 安全漏洞 → 缓解：使用 `__builtins__: None` 禁用内置函数，仅允许 `result` 变量访问
- **注意**: BFS 层级遍历需要正确处理 ConditionNode 的运行时路由——ConditionNode 不在编译时确定层级，而是在运行时动态决定下一层
- **注意**: LoopNode 的 sub_nodes 重新编号：内部 node_id 加前缀避免与外部节点冲突（`{loop_node_id}_inner_{i}`）
- **注意**: 本 Story 不实现 HumanConfirmNode 的完整暂停/恢复逻辑——仅在 dispatch 中预留分支，S10 实现完整 HITL
