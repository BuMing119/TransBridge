# Story 01: ExecutionEngine 上帝类拆分

**所属方案**: `plans/smart-assistant-refactor/plan.md`
**技术模块**: backend
**状态**: 已实现
**创建日期**: 2026-05-22

## 前置依赖

### 上游 Story
- 无（本 Story 是首个拆分任务，无前置依赖）

### 跨 Plan 依赖
- 无

### 引用的架构决策
- ADR-008 (2026-05-22 更新节): 组合拆分模式，模块粒度规范（文件≤450行/类≤22方法）
- ADR-009: Reflexion 边界不变，`_execute_tool_with_retry` 保留在 ExecutionEngine

## 验收标准

（从 plan 原样复制）

- [ ] `condition_evaluator.py` 存在，包含 `ConditionEvaluator` 类（~252行，10个 `_eval_ast_*` 方法 + `eval_condition` + `_eval_compare_op` + `_resolve_isinstance_type`）
- [ ] `checkpoint_manager.py` 存在，包含 `CheckpointManager` 类（~79行，`save_checkpoint` / `load_checkpoint` / `checkpoint_path` / `_safe_serialize`）
- [ ] `graph_executor.py` 存在，包含 `GraphExecutor` 类（~606行） + `StepResult` dataclass，承接全部 BFS 执行/护栏链/重试/生命周期/回调注册
- [ ] `execution_engine.py` 从 888行 缩减至 ~84行，作为委托门面组合 `GraphExecutor`（含 ConditionEvaluator/CheckpointManager 重导出 + `__getattr__` 代理）
- [ ] `ExecutionEngine.__init__` 创建 `GraphExecutor` 实例（组合），暴露 `_condition_evaluator` / `_checkpoint_manager` 引用
- [ ] `execution_engine.py` 顶部重导出：`from .condition_evaluator import ConditionEvaluator` / `from .checkpoint_manager import CheckpointManager`
- [ ] 所有 BFS 执行路径行为不变（`execute_graph` → `_bfs_one_level` → `_dispatch_node` 调用链正常）
- [ ] `StepResult` 定义在 `graph_executor.py`（`execution_engine.py` 重导出），消除循环导入
- [ ] 现有测试全部通过（328/330 通过，2 个预存失败无关）

## 数据流

```
ExecutionEngine.execute()
    │
    ▼
ExecutionEngine.execute_graph(spec, steps)
    │
    ├── CheckpointManager.load_checkpoint(graph_id)     ← 恢复中断点
    │
    ▼
ExecutionEngine._bfs_one_level(nodes, level)
    │
    ├── ConditionEvaluator.eval_condition(condition, variables) ← 分支/条件求值
    │     ├── _eval_ast_node()  → 按节点类型分发
    │     │     ├── _eval_ast_constant()
    │     │     ├── _eval_ast_name()
    │     │     ├── _eval_ast_attribute()
    │     │     ├── _eval_ast_subscript()
    │     │     ├── _eval_ast_compare()
    │     │     ├── _eval_ast_boolop()
    │     │     ├── _eval_ast_unaryop()
    │     │     └── _eval_ast_call()
    │     └── _resolve_isinstance_type()
    │
    ├── ExecutionEngine._dispatch_node(node)              ← 分发到具体执行
    │     └── ExecutionEngine._run_single(step)
    │           ├── ExecutionEngine._run_guard_chain()     ← 护栏中间件
    │           └── ExecutionEngine._execute_tool_with_retry() ← 重试循环
    │
    └── CheckpointManager.save_checkpoint(graph_id, ...)  ← 保存中断点

组合关系（非继承，4层）:
  ExecutionEngine (委托门面, ~84行)
    └── ._executor: GraphExecutor (~606行, BFS调度+护栏+重试+生命周期+回调)
          ├── ._condition_evaluator: ConditionEvaluator (~252行)
          └── ._checkpoint_manager: CheckpointManager (~79行)
```

## 关键接口

### condition_evaluator.py

```python
class ConditionEvaluator:
    """AST 条件表达式求值器。从 ExecutionEngine 中提取，单一职责——对图节点的
    condition 字段做布尔求值。无状态，纯函数式。"""

    def eval_condition(self, condition: str, results: dict[str, StepResult]) -> bool:
        """入口：将 condition 字符串解析为 AST，求值后返回布尔值。
        原方法名: _eval_condition（改为公开 API）"""

    def _eval_ast_node(self, node, result, depth: int = 0) -> object:
        """根据 node 类型分发到具体求值方法（内部方法，保持私有）"""

    def _eval_ast_constant(self, node, _result, _depth: int) -> object:
        """字面量求值"""

    def _eval_ast_name(self, node, result, _depth: int) -> object:
        """变量名查找"""

    def _eval_ast_attribute(self, node, result, depth: int) -> object:
        """属性访问"""

    def _eval_ast_subscript(self, node, result, depth: int) -> object:
        """下标访问"""

    def _eval_ast_compare(self, node, result, depth: int) -> object:
        """比较运算（==, !=, <, >, in, is, is not）"""

    @staticmethod
    def _eval_compare_op(op, left, right) -> bool:
        """单个比较操作符求值"""

    def _eval_ast_boolop(self, node, result, depth: int) -> object:
        """布尔运算（and, or）"""

    def _eval_ast_unaryop(self, node, result, depth: int) -> object:
        """一元运算（not）"""

    def _eval_ast_call(self, node, result, depth: int) -> object:
        """函数调用求值（如 isinstance）"""

    def _resolve_isinstance_type(self, type_node, result) -> type:
        """解析 isinstance 调用的类型参数"""
```

### checkpoint_manager.py

```python
class CheckpointManager:
    """检查点持久化管理器。管理图执行中断点的保存与恢复。"""

    def __init__(self, checkpoint_dir: Path):
        """使用 ParatranzConfig.get_data_dir() / 'checkpoints' 作为默认目录"""

    def save_checkpoint(self, graph_id: str, current_node_id: str, state: dict) -> None:
        """保存当前执行进度到文件。原方法: _save_checkpoint"""

    def load_checkpoint(self, graph_id: str) -> dict | None:
        """加载检查点，无检查点时返回 None。原方法: _load_checkpoint"""

    def checkpoint_path(self, graph_id: str) -> Path:
        """计算检查点文件路径。原方法: _checkpoint_path"""

    @staticmethod
    def _safe_serialize(value) -> Any:
        """安全序列化——处理不可 JSON 序列化的对象。原方法: _safe_serialize"""
```

### execution_engine.py (修改后)

```python
class ExecutionEngine:
    """统一执行引擎（精简后）：BFS图执行 + 护栏链/重试 + 生命周期"""

    def __init__(self):
        self._condition_evaluator = ConditionEvaluator()
        self._checkpoint_manager = CheckpointManager(self._default_checkpoint_dir())
        # ... 原有其他初始化

    # === 图执行 ===
    def execute_graph(self, spec, steps) -> ...
    def _bfs_one_level(self, nodes, level) -> ...
    def _dispatch_node(self, node) -> ...
    def execute(self, ...) -> ...

    # === 护栏与重试 ===
    def _run_guard_chain(self, ...) -> ...
    def _run_single(self, step) -> ...
    def _execute_tool_with_retry(self, ...) -> ...

    # === 生命周期 ===
    def pause(self) -> ...
    def resume(self) -> ...
    def cancel(self) -> ...
    def shutdown(self) -> ...

    # === 回调 ===
    def on_step_started/finished/etc(self) -> ...
```

## 实现步骤

### 步骤 1: 创建 condition_evaluator.py

**涉及文件**: `src/transbridge/smart_assistant/condition_evaluator.py`（新建）

**实现要点**:
- 从 `execution_engine.py` 中完整复制 12 个 AST 求值方法
- 创建 `ConditionEvaluator` 类包裹这些方法
- `_eval_condition` 重命名为 `eval_condition`（公开入口，其他内部方法保持 `_eval_ast_*` 前缀）
- 类不持有任何实例状态（所有方法接收 `variables` dict 作为参数）
- 保留 `import ast` 和 `import logging`

**边界条件**:
- condition 为空字符串或 None → eval_condition 返回 False（fail-closed，与现有行为一致）
- AST 解析失败 → 捕获 SyntaxError，记录警告日志，返回 False（保守策略：不阻断执行但标记为条件不满足）
- variables 中缺少引用的变量 → 抛出 KeyError（与现有行为一致）
- `_AST_DISPATCH` 映射表 + `getattr(self, handler_name)` 调度机制整体迁移到 ConditionEvaluator：迁移后 `self` 即 ConditionEvaluator 实例，所有 `_eval_ast_*` 方法均在该实例上，`getattr` 调度正常工作，无需重新设计

**伪代码/设计思路**:
```python
# condition_evaluator.py
import ast
import logging
from typing import Any

logger = logging.getLogger(__name__)

class ConditionEvaluator:
    """AST 条件表达式求值器，从 ExecutionEngine 中提取"""

    def eval_condition(self, condition: str, results: dict) -> bool:
        if not condition or not condition.strip():
            return False  # fail-closed: 空条件视为不满足（与现有行为一致）
        try:
            tree = ast.parse(condition.strip(), mode='eval')
            return bool(self._eval_ast_node(tree.body, results))
        except Exception:
            logger.warning("条件求值失败: %s", condition, exc_info=True)
            return False

    # ... 11 个 _eval_ast_* 方法（原样复制，不改代码）
```

**测试策略**:
- 单测：`tests/smart_assistant/test_condition_evaluator.py`（新建）
  - 空条件 → 返回 False
  - 字面量比较 "True and False" → False
  - 变量查找 "step_1.success" → 根据 results dict 求值
  - 嵌套属性 "step_1.data.count > 0"
  - isinstance 调用 "isinstance(data, str)"
  - 语法错误条件 → 返回 False + 日志警告
  - None condition → 返回 False

### 步骤 2: 创建 checkpoint_manager.py

**涉及文件**: `src/transbridge/smart_assistant/checkpoint_manager.py`（新建）

**实现要点**:
- 从 `execution_engine.py` 中完整复制 4 个检查点方法
- 创建 `CheckpointManager` 类包裹
- `__init__` 接收 `checkpoint_dir: Path` 参数
- 内部方法 `_safe_serialize` 保持为 `@staticmethod`
- 原 `_save_checkpoint/_load_checkpoint/_checkpoint_path` 去掉下划线前缀，成为公开方法

**边界条件**:
- checkpoint 目录不存在 → `save_checkpoint` 自动创建（`mkdir(parents=True, exist_ok=True)`）
- checkpoint 文件损坏（JSON 解析失败）→ `load_checkpoint` 返回 None + 日志警告
- graph_id 包含特殊字符 → `checkpoint_path` 使用正则白名单消毒 `re.sub(r'[^a-zA-Z0-9_.-]', '_', graph_id)`（保留与现有实现一致的安全级别）

**伪代码**:
```python
# checkpoint_manager.py
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

class CheckpointManager:
    def __init__(self, checkpoint_dir: Path):
        self._dir = checkpoint_dir

    def save_checkpoint(self, graph_id: str, current_node_id: str, state: dict) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self.checkpoint_path(graph_id)
        payload = {
            "graph_id": graph_id,
            "current_node_id": current_node_id,
            "state": {k: self._safe_serialize(v) for k, v in state.items()},
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_checkpoint(self, graph_id: str) -> dict | None:
        path = self.checkpoint_path(graph_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("检查点文件损坏，跳过: %s", e)
            return None

    def checkpoint_path(self, graph_id: str) -> Path:
        import re
        safe_id = re.sub(r'[^a-zA-Z0-9_.-]', '_', graph_id)
        return self._dir / f"{safe_id}.json"

    @staticmethod
    def _safe_serialize(value) -> Any:
        # 原样复制
        ...

**测试策略**:
- 单测：`tests/smart_assistant/test_checkpoint_manager.py`（新建）
  - save + load 往返 → 数据一致
  - 不存在的检查点 → load 返回 None
  - 空 state dict → 正常保存/加载
  - graph_id 含特殊字符 → 文件名 sanitize 正确
  - 损坏的 JSON 文件 → load 返回 None + 日志警告

### 步骤 3: 精简 execution_engine.py

**涉及文件**: `src/transbridge/smart_assistant/execution_engine.py`（修改）

**实现要点**:
- 删除已迁移到 condition_evaluator.py 的 12 个 AST 求值方法
- 删除已迁移到 checkpoint_manager.py 的 4 个检查点方法
- 在 `__init__` 中创建 `ConditionEvaluator` 和 `CheckpointManager` 实例
- 将所有 `self._eval_condition(condition, results)` 调用替换为 `self._condition_evaluator.eval_condition(condition, results)`
- 将所有 `self._save_checkpoint(...)` 等调用替换为 `self._checkpoint_manager.save_checkpoint(...)`
- 顶部添加重导出 import

**调用替换映射**:
| 原调用 | 新调用 |
|--------|--------|
| `self._eval_condition(c, r)` | `self._condition_evaluator.eval_condition(c, r)` |
| `self._save_checkpoint(gid, nid, s)` | `self._checkpoint_manager.save_checkpoint(gid, nid, s)` |
| `self._load_checkpoint(gid)` | `self._checkpoint_manager.load_checkpoint(gid)` |
| `self._checkpoint_path(gid)` | `self._checkpoint_manager.checkpoint_path(gid)` |
| `self._safe_serialize(v)` | `CheckpointManager._safe_serialize(v)` |

**边界条件**:
- `_eval_condition` 在 ExecutionEngine 内部被调用的位置：`_bfs_one_level`（条件节点判断）、`_dispatch_node`（分支路由）→ 全部替换
- `_save_checkpoint` 在 `execute_graph` 中被调用 → 替换
- `_load_checkpoint` 在 `execute_graph` 入口被调用 → 替换
- 确保 `checkpoint_manager.py` 不再引用 `ParatranzConfig`（如果原代码有引用），改为接收 `Path` 参数

**测试策略**:
- 全量回归测试，确保拆分后所有执行路径行为不变

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/smart_assistant/condition_evaluator.py` | 新建 | ConditionEvaluator 类，12 方法，~200行 |
| `src/transbridge/smart_assistant/checkpoint_manager.py` | 新建 | CheckpointManager 类，4 方法，~150行 |
| `src/transbridge/smart_assistant/execution_engine.py` | 修改 | 888→84行，委托门面（组合 GraphExecutor + 重导出） |
| `src/transbridge/smart_assistant/graph_executor.py` | 新增 | GraphExecutor 类 + StepResult dataclass，~606行，BFS 执行逻辑 |
| `src/transbridge/smart_assistant/__init__.py` | 修改 | StepResult 懒加载映射 → `.graph_executor` |
| `tests/smart_assistant/test_condition_evaluator.py` | 新建 | 条件求值器单元测试（≥8用例） |
| `tests/smart_assistant/test_checkpoint_manager.py` | 新建 | 检查点管理器单元测试（≥5用例） |

## 风险与注意事项

- **风险 1**: `_eval_condition` 方法名内联调用改为 `self._condition_evaluator.eval_condition(...)` 后可能遗漏调用点 → 缓解：全局搜索 `_eval_condition` 确保无遗漏
- **风险 2**: `_save_checkpoint` 中可能引用了 `self` 上的其他属性（如 ParatranzConfig 路径）→ 缓解：提取时仔细检查方法体中对 `self` 的引用，将所需状态作为构造函数参数传入
- **注意 1**: `StepResult` 保留在 execution_engine.py，不迁移——StepResult 是图执行的结果载体，与执行引擎内聚。`_SignalBridge` 实际位于 `conversation_orchestrator.py:16`，不在本 Story 范围内
- **注意 2**: `_eval_compare_op` 是 `@staticmethod`，迁移到 ConditionEvaluator 后保持静态方法
