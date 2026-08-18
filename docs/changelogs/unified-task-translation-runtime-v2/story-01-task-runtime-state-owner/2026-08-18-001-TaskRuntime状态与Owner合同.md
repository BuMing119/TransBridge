# Story 01：TaskRuntime 状态、Owner 与订阅合同

- 日期：2026-08-18
- Epic/Story：`unified-task-translation-runtime-v2/S01`
- 追溯：FR20.1～20.3/20.6、ADR-019、R-027～R-030
- 状态：实现完成，增量验证通过；待综合 QA

## 实现增量

- 新增冻结 JobSpec、JobCapabilities、OwnerRef、JobSnapshot、JobEvent、TaskEventFilter、TransitionError 和幂等 Subscription。
- `TaskRuntime` 通过注入 Clock/Id 生成 run identity；在锁内校验 owner/revision/transition 并提交状态，在锁外分发只读事件。
- 固定 queued→running↔paused、running/paused→cancelling→cancelled、running/paused→completed/failed 和 queued→cancelled；三个终态互斥且不可覆盖。
- pause/resume/cancel 仅在 JobSpec 声明能力时可用；显式 `tasks:manage` 权限可跨 owner scope，伪造或 owner 不匹配引用被拒绝。
- 订阅保存实际注册 token，close/dispose 可重入且可从 callback 内执行；回调异常不改变任务状态。
- 旧 `TaskManager` 改为 TaskRuntime facade/projection：旧 register/pause/resume/cancel/finished 回调保留，任意 `set_status` 被拒绝，deprecated callback wrapper 可由原 callback 真正移除，取消后的迟到 success 通知不再覆盖或冒充完成。

## 验证证据

- `uv run --locked python -m pytest tests/contracts/test_task_runtime.py tests/smart_assistant/test_task_runtime_facade.py tests/smart_assistant/postprocess/test_task_manager.py -q -p no:cacheprovider`：50 passed。
- 新增 application/tasks 与测试文件 Ruff/format 全部通过；既有大型 TaskManager 文件执行 E9/F63/F7/F82 静态门禁通过，未借机批量格式化历史代码。
- [EvidenceManifest](../../../test-reports/requirement-code-review-2026-08-18/qa-evidence/task-runtime-s01/qa-20260818T065602.544769Z-86cba2788984/manifest.json)：`passed`，schema/verdict/hash 复验有效。
- 未执行 Git commit/push。

## 剩余门禁

旧 TaskHandle 仍是兼容 projection，业务 backend 尚未迁移；cooperative cancellation token、commit barrier、stop/shutdown、并发配额和 backend adapter 由 S02 承接。Checkpoint、Translation/PostProcess workload、Task Monitor 与入口集成由 S03～S07 承接，最终删除旧 facade 需通过跨入口 parity 和综合 QA。
