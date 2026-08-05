# 001: ExecutionEngine 上帝类组合拆分

**日期**: 2026-05-22
**类型**: 增/改
**关联**: Epic: Smart Assistant 超重文件拆分重构 > Story 01: ExecutionEngine 上帝类拆分

## 修改文件

### `src/transbridge/smart_assistant/condition_evaluator.py` (增)
- **修改内容**: 新建 ConditionEvaluator 类，承接 ExecutionEngine 中全部 12 个 AST 条件求值方法（`eval_condition` 公开入口 + 11 个 `_eval_ast_*` 内部方法），以及类属性 `_AST_DISPATCH` 映射表、`_SAFE_TYPE_WHITELIST`、`_MAX_EVAL_DEPTH=20`。空条件返回 `False`（fail-closed），AST 解析失败返回 `False` + 日志警告
- **原因**: 37 方法的 ExecutionEngine 上帝类是最严重的维护风险。AST 求值器天然独立——输入 condition 表达式 + 变量上下文，输出布尔值，无副作用，适合作为独立组件提取

### `src/transbridge/smart_assistant/checkpoint_manager.py` (增)
- **新建内容**: CheckpointManager 类，承接 `save_checkpoint`/`load_checkpoint`/`checkpoint_path`/`_safe_serialize` 4 个检查点方法。构造器接收 `Path` 参数（不再引用 ParatranzConfig），`checkpoint_path` 使用 `re.sub(r'[^a-zA-Z0-9_.-]', '_', graph_id)` 正则白名单消毒
- **原因**: 检查点持久化仅与文件系统交互，与执行引擎核心逻辑（BFS 调度）无关。独立后可单独测试路径消毒、JSON 损坏容错等边界条件

### `src/transbridge/smart_assistant/execution_engine.py` (改)
- **修改内容**: 删除已迁移的 12 个 AST 求值方法 + 4 个检查点方法（移除约 272 行）。`__init__` 中新增 `self._condition_evaluator = ConditionEvaluator()` 和 `self._checkpoint_manager = CheckpointManager(...)` 组合实例。所有 `self._eval_condition(...)` 调用替换为 `self._condition_evaluator.eval_condition(...)`，检查点调用同理。顶部添加 ConditionEvaluator/CheckpointManager 重导出
- **原因**: 引擎自身聚焦于 BFS 执行调度核心职责，AST 求值和检查点管理通过组合委托给专职组件

### `src/transbridge/smart_assistant/__init__.py` (改)
- **修改内容**: `__all__` 和 `_SYMBOL_MODULES` 懒加载映射表新增 `ConditionEvaluator`、`CheckpointManager` 两个符号
- **原因**: 新组件作为公开 API 供外部使用，需支持惰性导入

### `tests/smart_assistant/test_execution_engine.py` (改)
- **修改内容**: 3 个条件求值测试用例更新调用路径：`self.engine._eval_condition(...)` → `self.engine._condition_evaluator.eval_condition(...)`
- **原因**: 方法迁移到 ConditionEvaluator 后，测试需通过组合访问路径调用
