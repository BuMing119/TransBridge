# Story 02：取消提交屏障、Stop/Shutdown 与 Backend Adapters

- 所属 Plan：[Unified Task and Translation Runtime V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR20.4/20.5/20.7、NFR1.2；ADR-004/019；R-027/R-030
- 依赖：S01

## 目标与验收

取消后不再 completed、不提交迟到结果；stop 保留策略化 checkpoint；shutdown 拒绝新任务并释放资源；QThread/thread pool 只做 backend；Monitor 按 capability 展示。

> 实现记录：[2026-08-18-001-取消提交屏障与 Backend 合同](../../../docs/changelogs/unified-task-translation-runtime-v2/story-02-cancel-shutdown-backends/2026-08-18-001-取消提交屏障与Backend合同.md)。主线正式 uv 定向回归 78 passed，EvidenceManifest 复验有效；checkpoint 持久化仍由 S03 承接。

## 事件顺序与接口

cancel → running/paused→cancelling → CancellationToken set → workload 停止新副作用 → backend returns → commit guard 检查 run_id/terminal/revision → cancelled。`StopPolicy` 决定 checkpoint；`shutdown(grace, policy)` 先 close admission，再 drain/cancel，最后 backend close。Backend Protocol 仅 `start/cancel_hint/join/close`。

## 实施步骤

1. 定义 CancellationToken/CommitPermit，所有外部请求、批次和 publish 前检查。
2. 实现 thread/threadpool backend；QThread wrapper 只转信号，不捕获业务终态。
3. 分开 cancel、stop、shutdown 语义和 capability；不支持 pause 的任务不暴露按钮。
4. late result 经 runtime `try_commit`，终态或 run_id 不匹配记 ignored event。
5. 应用退出调用 runtime shutdown，资源 lease 按逆序释放。

## 边界、迁移与测试

无法强杀的阻塞调用在 grace 超时后标 failed/cancel pending diagnostic，不能伪称已释放。测试取消/异常/完成 race、暂停时取消、shutdown submit、join timeout、后台异常、UI dispose；受控 100 条假 LLM 最大活动≤3、取消到停止新副作用 P95≤1s。
