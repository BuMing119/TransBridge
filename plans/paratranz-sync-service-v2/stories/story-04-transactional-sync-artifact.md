# Story 04：事务同步、部分失败与 Artifact 原子发布

- 所属 Plan：[ParaTranz Sync Service V2](../plan.md)
- 状态：已实现并增量验证通过（2026-08-18）
- 追溯：FR22.4、FR17.3、NFR2.1；ADR-017/019；R-042
- 依赖：S01～S03、TaskRuntime S01～S03、I/O S06、persistence UoW

## 目标与验收

同步在隔离副本执行；部分失败可定位并保留重试依据；取消/迟到批次不提交；Artifact 校验后原子发布；重试不重复已成功项。

## 事件顺序与接口

confirmed SyncPlan → TaskRuntime workload → batch remote operations → `SyncItemOutcome` journal/checkpoint → candidate local aggregate merge → validate/revision check → UoW commit；Artifact 为 trigger→poll→download `.part`→hash/content validate→PublishCoordinator。`RetryToken` 绑定 plan/item outcomes。

## 实施步骤

1. executor 只按确认 plan 操作，逐 item 记录 success/failure/skipped/remote revision。
2. 下载合并使用临时 aggregate，全部可接受后单次 ChangeSet/UoW；上传 partial 不伪成功。
3. checkpoint 保存 idempotency key/remote ref，恢复跳过已确认成功项。
4. cancellation/terminal guard 放在每个请求和本地/Artifact commit 前。
5. GUI/Agent/MCP 由同一 OperationResult 展示 counts/diagnostics/retry token。

## 测试、边界与回退

受控服务执行真实成功链及中途 429/500/断线；磁盘/校验/replace fault、取消 race、重复恢复、远端部分提交。断言本地正式集合不半合并、旧 artifact 保留、结果跨入口一致。旧 uploader/downloader/artifact 作为 adapter 可回退，不允许直接正式写。
