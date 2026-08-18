# Story 03：Project/Variant 两阶段生命周期与 UnitOfWork

- 所属 Plan：[Project Session Persistence V2](../plan.md)
- 状态：已实现并增量验证通过（2026-08-18）
- 追溯：FR19.4、NFR2.1；ADR-018；R-023
- 依赖：S01/S02、I/O S06

## 目标与验收

新建、打开、关闭、切换、保存、快照和导出任一步失败时保持旧活动上下文可用；取消保存不切换；快照不污染 current 指针。

## 事件顺序与接口

command → `prepare_transition`：检查 dirty/询问策略、保存旧 aggregate、加载并验证目标、materialize candidate → `PreparedTransition(token, old_ref, candidate, leases)` → `commit`：repository UoW 原子保存引用并交换 active aggregate → 发布事件/projection；异常执行 rollback/释放 candidate。

计划接口：`ProjectLifecycleService`、`UnitOfWork.begin/commit/rollback`、`DirtyDecision(save/discard/cancel)`、`PreparedTransition`（一次性、owner-bound）。

## 实施步骤

1. 从 MainWindow/workbench 提取 lifecycle use cases，UI 只提供 DirtyDecision 和展示 diagnostic。
2. Repository UoW 在提交前不修改 workspace.active/project.active_variant。
3. 快照 load 以只读 source ref 物化，保存仍指向正式 Variant。
4. 导出取得一致 aggregate revision；执行中 revision 变化按策略失败/重试。
5. 成功 commit 后再发 projection 事件；回调异常不回写 domain state。

## 边界、迁移与测试

双击切换、并发任务 lease、保存失败、源文件失效、target schema 错误和用户取消都需覆盖。旧 MainWindow 方法逐个变 facade，可按 use case 回退但不恢复“先改 active id”。fault injection 在 prepare/load/save/reference swap/event 各点验证旧项目可继续编辑；另测 Unicode/长路径、空/多源项目、快照 current 指针。

## 2026-08-18 无变化激活补充

- `prepare_transition` 仍执行目标校验和隔离物化；但当 Project/Variant 身份、活动指针及待持久化 revision 均未变化时，commit 必须把它识别为幂等激活，不递增 Project revision，也不重写未变化的 Project/Variant JSON。
- 必须区分“重新发布必要的只读 projection”与“持久化业务 mutation”：前者可用于恢复 GUI 视图，不能成为调用 Repository `save` 的理由。确有 dirty revision、活动 Variant 变化、迁移结果或业务字段变化时，仍按原 UnitOfWork 原子提交。
- 使用记录 Repository/UoW 调用的测试覆盖重复打开同一 Project、重复激活同一 Variant和切换后再次激活；断言无变化路径零持久化写入、revision 不变，真实变化路径仍保存且 fault injection/rollback 合同不变。
