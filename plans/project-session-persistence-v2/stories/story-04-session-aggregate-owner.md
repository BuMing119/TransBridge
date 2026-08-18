# Story 04：Session Aggregate、完整恢复与 Owner 隔离

- 所属 Plan：[Project Session Persistence V2](../plan.md)
- 状态：已实现并增量验证通过（2026-08-18）
- 追溯：FR19.5/19.6、FR20.3；ADR-018/019；R-025/R-026/R-029
- 依赖：S01/S03；TaskRuntime S01 接口（实现可并行，集成后验收）

## 目标与验收

恢复 UI 对话、后端推理历史、项目/Variant、审批和 Task refs；切换先保存后激活；旧 Session 的迟到事件不能污染新 Session；`python -O` 不改变状态校验。

## 数据流与接口

`SwitchSession(target)` → save current aggregate/revision → load+validate target → resolve project/variant refs → reconcile Task JobRefs/approvals → rehydrate controller state → commit active session → render projection。`SessionAggregate` 包含 messages、backend history/summary、controller state、OwnerRef、active refs、pending approvals、job refs、revision。

## 实施步骤

1. 用显式 transition table/domain error 替换 SessionController `assert`；定义可恢复与不可恢复状态。
2. SessionRepository 保存完整 aggregate，不缓存向调用方暴露的可变 dict。
3. Task/approval/event 必须匹配 Session OwnerRef + run_id + revision；不匹配记录 ignored diagnostic。
4. 两阶段 switch 复用 lifecycle token，commit 后才更新 active id/UI。
5. 恢复缺少 backend history 时返回 degraded，不展示为完全恢复。

## 边界、迁移与测试

旧 session 仅含 messages 时迁移为 degraded 并提示；损坏 task ref 不阻断可读对话但不能恢复运行。测试覆盖重启、切换保存失败、迟到完成/取消、pending approval、内部 ID 欺骗、`python -O` 非法 transition、500 轮创建/切换/销毁和 listener 释放。回退保留旧文件备份，不允许 UI-only 伪恢复。
