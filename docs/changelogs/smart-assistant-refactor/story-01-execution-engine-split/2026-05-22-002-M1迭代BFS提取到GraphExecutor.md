# 002: M1 迭代 — BFS 执行逻辑提取到 GraphExecutor

**日期**: 2026-05-22
**类型**: 增/改
**关联**: Epic: Smart Assistant 超重文件拆分重构 > Story 01: ExecutionEngine 上帝类拆分

## 修改文件

### `src/transbridge/smart_assistant/graph_executor.py` (增)
- **修改内容**: 新建 GraphExecutor 类（~606行），从 ExecutionEngine 中提取全部 BFS 图执行逻辑——`execute`/`execute_graph`/`_bfs_one_level`/`_dispatch_node`/`_run_single`/`_run_guard_chain`/`_execute_tool_with_retry`/`_await_decision`，以及生命周期方法（`pause`/`resume`/`cancel`/`shutdown`）、回调注册（6 个 `on_*` 方法）、决策注入（`provide_decision`）、`__init__`（创建 ConditionEvaluator/CheckpointManager 组合实例）。同时定义 `StepResult` dataclass（从 execution_engine.py 迁移以解决循环导入）
- **原因**: QA 审查发现 execution_engine.py 610 行 vs 目标 ≤450（超标 160 行/Major M1）。BFS 调度逻辑（~400行）独立为 GraphExecutor 后，ExecutionEngine 缩减为 84 行委托门面

### `src/transbridge/smart_assistant/execution_engine.py` (改)
- **修改内容**: 从 610行 缩减至 84行。ExecutionEngine 变为委托门面——`__init__` 创建 `GraphExecutor` 实例，所有公开方法（`execute`/`execute_graph`/`pause`/`resume`/`cancel`/`shutdown`/`provide_decision`/6个`on_*`回调注册）委托给 `self._executor`。`_condition_evaluator` 和 `_checkpoint_manager` 引用从 GraphExecutor 获取。新增 `__getattr__` 代理未命中属性到 GraphExecutor（向后兼容测试/内部访问）。保留 ConditionEvaluator/CheckpointManager 重导出和类属性（`_MAX_WORKERS` 等）。`StepResult` 从 `graph_executor` 重导出
- **原因**: 888→610→84 行，三层迭代（原始→Story01→M1），Engine 从上帝类变为纯委托门面

### `src/transbridge/smart_assistant/__init__.py` (改)
- **修改内容**: `_SYMBOL_MODULES` 中 `StepResult` 映射从 `".execution_engine"` 改为 `".graph_executor"`
- **原因**: StepResult 定义移至 graph_executor.py，消除 execution_engine ↔ graph_executor 循环导入

### `tests/smart_assistant/test_execution_engine.py` (改)
- **修改内容**: `test_executor_reused` 中 `self.engine._executor.submit(...)` 改为 `self.engine._executor._executor.submit(...)`（`_executor` 现指向 GraphExecutor，ThreadPoolExecutor 在其下一层）
- **原因**: ExecutionEngine._executor 语义变更——现在指 GraphExecutor 实例而非 ThreadPoolExecutor

### 文档同步 (改)
- `docs/adr/008-smart-assistant-code-layering.md` — D1 节 3模块→4模块，新增 graph_executor.py，更新行数目标与影响分析
- `plans/smart-assistant-refactor/plan.md` — Story 01 AC 新增 graph_executor.py 验收项，更新行数目标
- `plans/smart-assistant-refactor/stories/story-01-execution-engine-split.md` — AC/数据流/文件变更表同步更新
