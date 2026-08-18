# Story 01：TaskRuntime 核心状态机、Owner 与订阅

- 所属 Plan：[Unified Task and Translation Runtime V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR20.1～20.3/20.6；ADR-019；R-027～R-030
- 依赖：platform S02/S03

## 目标与验收

建立 queued/running/paused/cancelling/cancelled/completed/failed 的唯一状态机；非法迁移、owner 不匹配和终态覆盖被拒绝；Subscription 能真正移除 wrapper；同步结果与 Deferred JobRef 类型稳定。

## 接口与状态流

`submit(JobSpec, OwnerRef)` → `JobRef(run_id)` → queued→running→单一终态；pause/resume 仅在 capability 存在时流转。计划类型：冻结 `JobSpec`、不可伪造 `JobRef`、只读 `JobSnapshot`、`JobCapabilities`、`OwnerRef(entrypoint/session/project)`、`Subscription.close()`、`TransitionError`。状态仅由 TaskRuntime transition table 写入。

## 实施步骤

1. 新增 `application/tasks/models.py/runtime.py/events.py`，锁内完成 compare-and-transition，锁外分发事件。
2. run_id 使用注入 IdPort；任务显示名不参与身份。
3. query/control 校验 owner 或显式管理权限；事件携带 sequence/revision。
4. Subscription 保存实际注册 token/wrapper，close 幂等且可在回调中调用。
5. TaskManager 变 facade；`set_status` 不再公开任意写，旧 completed/failed 回调映射统一 finished event。

## 边界、迁移与测试

回调异常不得改变任务终态；历史 snapshot 只读；优化模式不可依赖 assert。属性测试遍历所有状态/动作，覆盖并发终态 race、owner 隔离、listener 回归、重复 close、sync/deferred 类型和事件顺序。旧 API 可暂时委托 runtime，删除需 S07 parity。
