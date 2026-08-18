# Task Runtime 兼容清单、Parity 证据与删除门禁

- 关联 Plan：`unified-task-translation-runtime-v2`（S07）
- 日期：2026-08-18
- 状态：S07 提供生产入口桥接与只读投影；公开旧 API 本 Story 不删除

## 1. 目标

统一 TaskRuntime 成为任务状态唯一权威后，所有生产入口（GUI/Agent/MCP）应取得 `Deferred[JobRef]`，Session 以 `JobRef/run_id` 匹配完成事件。本清单记录旧 `TaskManager`/`MixedWorker`/Graph 状态 API 的调用方、parity 证据与删除门禁，保证渐进迁移不破坏既有入口，也不伪称删除完成。

## 2. 新生产路径（S07 已提供）

| 合同 | 位置 | 说明 |
|---|---|---|
| 提交入口 | `application/tasks/entrypoints.py::RuntimeTaskBridge.submit` | 返回 `Deferred[JobRef]`（AWAITING_TASK 路径），经 `runtime.schedule` 执行 workload，终态由 runtime 唯一决定 |
| 终态等待 | `RuntimeTaskBridge.wait_terminal` | 轮询 `JobSnapshot` 到终态，映射 `TerminalOutcome`（含 outcome/diagnostics） |
| 公共序列化 | `RuntimeTaskBridge.to_operation_result` | `OperationResult`（COMPLETED 携带 snapshot；FAILED/CANCELLED 不带 value，符合操作合同） |
| 只读投影 | `application/tasks/projection.py::job_snapshot_to_view` | GUI/Agent/MCP 共用同一 public view；保留旧 Monitor 键（task_id/status/progress/metadata/created_at） |
| 能力控制 | `RuntimeTaskProjection.control` | pause/resume/cancel 走 runtime，按 capability 与状态启用；cleanup 为视图本地动作 |
| Session 门禁 | `application/tasks/entrypoints.py::SessionJobGate` | 只接受活动 session 的 FINISHED 事件；旧 session 迟到事件仅审计（`audited()`），绝不改变状态 |

## 3. 旧 API 调用清单（本 Story 不删除）

| 调用方 | 位置 | 使用的旧 API | 迁移状态 |
|---|---|---|---|
| `tool_translator.py` | 3 个翻译工具（start/polish/写入链） | 兼容 `TaskManager` facade、`is_long_running=True` | facade 已绑定 Composition Root 的同一 TaskRuntime；候选提交不再拥有独立终态 |
| `tool_proofreader.py` | postprocess 工具 | 兼容 `TaskManager` facade、`is_long_running=True` | 已切 candidate-only workload 与唯一 commit；异步 run_id 经 ToolExecutionHandler 进入 Session gate |
| `tool_paratranz.py` | upload/download/export 工具 | `is_long_running=True`（3 处） | 经通用 ToolExecutionHandler 使用同一 TaskRuntime；ParaTranz service 保持业务 adapter |
| `tool_writer.py` | write_back 工具 | `is_long_running=True` | 经通用 ToolExecutionHandler 使用同一 TaskRuntime；writer 保持 I/O adapter |
| `graph_executor.py` | ExecutionContext | 兼容 `TaskManager` 注入 | GraphWorkloadAdapter 已接统一 runtime 终态；旧构造仅作兼容入口 |
| `session_controller.py` | `handle_task_started` / `handle_task_completed` | 状态机 EXECUTING→AWAITING_TASK→THINKING/IDLE | 生产调用使用实际 JobRef/run_id；仅 active session 可接收终态，迟到事件忽略并审计 |
| `task_manager.py` facade | 全项目 | `set_status/notify_finished/list_active/reset` 等 | 绑定 `AppRuntime.tasks`；status/progress/list 从 JobSnapshot 投影，外部 runtime 生命周期不由 facade 关闭 |

## 4. Parity 证据

- 旧 `TaskManager` 公开形状保持兼容，但内部状态已委托统一 runtime；S07 综合回归 146 passed。
- S07 合同测试（`tests/contracts/tasks/`，11 项）证明：新桥接的提交→终态→`OperationResult` 与旧同步 `OperationResult` 类型区分（`Deferred` vs 同步值）不变；投影视图与旧 Monitor 键完全兼容；控制按 capability 拒绝；Session 门禁只读且审计旧事件。
- 相同 `run_id` 下，runtime snapshot、投影视图、`TerminalOutcome`、`OperationResult` 的状态/计数一致（由同一 `JobSnapshot` 派生，无 writable mirror）。

## 5. 删除门禁（后续 Story 满足后才允许移除旧路径）

1. 每个旧调用方已切换到 `RuntimeTaskBridge.submit` 并取得 `JobRef`；
2. `session_controller.handle_task_*` 生产调用已改为 `SessionJobGate` 事件匹配；
3. `TaskManager.set_status` 无生产写调用（只读 facade 或已删除）；
4. `is_long_running` 只标记真正的后台工具，且工具返回 `Deferred`；
5. `graph_executor` 不再以 `TaskManager()` 注入上下文；
6. 跨 GUI/Agent/MCP 端到端 parity 测试通过（综合 QA 承接）。

公开 facade 的物理删除门禁尚未全部满足，因此继续保留；其生产状态权威已经迁移，不回退统一 Job 状态。
