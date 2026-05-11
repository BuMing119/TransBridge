# 001: Story-10 Checkpoint 与人机协同编码实现

**日期**: 2026-05-10
**类型**: 增/改
**关联**: Epic: Agent框架升级 > Story 10: Checkpoint 与人机协同

## 修改文件

### `src/transbridge/smart_assistant/graph_types.py` (改)
- **修改内容**: 新增 Checkpoint 数据类（graph_id/current_node_id/completed_results/graph_state/timestamp），含 to_dict() 序列化和 from_dict() 反序列化方法。StepResult.data 序列化前执行类型校验——仅允许 dict/list/str/int/float/bool/None，不可序列化对象跳过并写警告日志。
- **原因**: FR7.13.7.5 图执行状态可序列化。Checkpoint 是断点恢复的基础设施。

### `src/transbridge/smart_assistant/execution_engine.py` (改)
- **修改内容**: 四处变更：
  1. execute_graph() 每层执行后自动 save_checkpoint() 到 `data/projects/{project}/{variant}/checkpoints/{graph_id}_{timestamp}.json`
  2. 新增 load_checkpoint() / resume_from_checkpoint()——从 checkpoint 恢复，跳过已完成节点
  3. HumanConfirmNode 执行流程：暂停→发 node_paused 信号→后台线程 QEventLoop local loop 等待→UI 确认→provide_decision()→退出 QEventLoop→继续
  4. _paused Event 真正生效：在 BFS 循环中添加 `_check_pause()` 调用
- **原因**: FR7.13.7.4 人机协同 + FR7.13.7.5 状态持久化。暂停/恢复使长时间运行的图可被用户控制，Checkpoint 使异常中断后可恢复。

### `src/transbridge/ui/tools/smart_assistant/chat_widget.py` (改)
- **修改内容**: 连接 ExecutionEngine 的 node_paused/node_resumed 信号，实现确认弹窗交互——显示提示文本和选项列表，用户选择后调用 provide_decision() 恢复执行。
- **原因**: FR7.13.7.4 HITL UI 层实现。HumanConfirmNode 到达时用户可通过确认弹窗做出决策，超时采用 default_choice 兜底。

---

## Phase 2 QA 安全修复（归入本 Story）

### `src/transbridge/smart_assistant/execution_engine.py` (改 — QA 修复)
- **修改内容**: 三处安全修复：
  1. **_eval_condition 替换 eval 沙箱**：原 `eval(condition, {"__builtins__": None})` 可被绕过。替换为 AST 白名单求值器 `_eval_ast_node()`，仅允许 Constant/Name/Attribute/Subscript/Compare/BoolOp/UnaryOp/Tuple/Call(仅.get()) 等安全节点
  2. **_checkpoint_path 路径穿越防御**：graph_id 新增 `re.sub(r'[^a-zA-Z0-9_.-]', '_', graph_id)` sanitize，阻止 `../` 路径穿越
  3. **移除 execute() 旧版死代码**：原 DAG 拓扑排序版 execute() 和 _topological_levels() 移除（~60行），统一到 GraphSpec 版
- **原因**: QA 审查发现 2 Blocker（eval 沙箱逃逸 + 路径穿越）和 1 Major（死代码），修复后通过安全审查。
