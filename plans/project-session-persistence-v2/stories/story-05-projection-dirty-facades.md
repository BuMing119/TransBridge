# Story 05：Projection、Dirty 与兼容 facade 迁移

- 所属 Plan：[Project Session Persistence V2](../plan.md)
- 状态：已确认（2026-08-18）
- 追溯：FR19.1～19.6、FR17.4；ADR-018；R-008/R-012
- 依赖：S02～S04、platform S03

## 目标与验收

每个业务字段只有一个权威写入点；AppContext/Step2/旧 stores 变只读 projection 或 command facade；projection 可重建且释放订阅，旧新路径结果等价。

## 数据流与接口

Aggregate command → domain mutation/revision/event → ProjectionStore reducer → Qt AppContext signal/UI render。UI 编辑通过 use case command，不直接改 `_entry_labels/_filter_state/entry.translation`。`DirtyState` 由 saved revision 与 aggregate revision 比较，不由任意 signal 推断。

## 实施步骤

1. 列出 AppContext、Step2、VariantStore、Session Panel 的字段 owner/caller，建立迁移清单。
2. 新增 projection subscription/reducer，AppContext properties 返回 defensive snapshot；setter 变 command adapter/弃用。
3. collection_changed 不再隐式 mark dirty，成功 ChangeSet/aggregate event 更新 revision。
4. 页面/Session 销毁调用 Subscription.close，禁止全局 reset runtime。
5. 每迁移一条调用链运行旧 facade/new use case parity，满足删除门禁前保留公开入口。

## 边界、迁移与测试

projection 事件丢失可从 aggregate snapshot 全量重建；UI 回调异常不回滚 domain commit但产生 diagnostic。测试用运行时 identity/写入审计证明无 writable mirror，覆盖重建、乱序/重复事件、unsubscribe/leak、dirty save/clear、旧 facade parity。回退可恢复旧 UI adapter，但不可恢复第二套业务状态。
