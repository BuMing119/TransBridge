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

## 2026-08-18 大项目投影与 GUI 渲染补充

### Snapshot 所有权与复制边界

- `ProjectionSnapshot` 在构造时递归冻结 JSON-like values；`ProjectionStore` 保存并向订阅方共享同一不可变 snapshot。`snapshot()`、事件分发和 AppContext 只读访问不得为约 20k entries 重复 `deepcopy` 或重复冻结整棵投影。
- 只有显式要求可变边界（例如兼容 facade 的 `to_dict()`）才生成独立副本；UI 列表渲染优先消费不可变序列/只读 view。该优化不得把 projection 暴露成可写业务状态，也不得放松 defensive snapshot 的外部兼容合同。

### QTableWidget 自动完整装填

1. 后台线程完成解析、物化和 projection 构建后，仅通过 queued Qt signal 把不可变结果交给 GUI 线程；任何 `QTableWidget`/`QTableWidgetItem` 创建与修改均留在 GUI 线程。
2. `_populate_table()` 先同步显示首批行并初始化 `0..total` 进度；后续由 `QTimer.singleShot(0, ...)` 在事件循环空隙自动追加固定大小批次，直到当前筛选结果全部装填。
3. 用户不需要点击“加载更多”，也不以滚动到表格底部触发下一批；“分批”仅是 GUI 调度策略，最终可见行数必须自动达到过滤后总数。
4. 每次刷新递增 render generation；旧 timer callback 发现 generation 失效即退出，防止项目切换、筛选或关闭期间把旧条目写入新视图。装填期间持续更新进度，完成后恢复 100%；列宽只按首批可见行计算，禁止在完成时重新扫描全部行。
5. Projection 通知按语义拆分：只有标签库或 entry labels 内容真实变化时才发出 `label_data_changed`；单条译文、stage、revision 或 dirty 变化不得触发 Step2 全表重建。
6. Qt 编辑回调跨越同步 command/projection 通知边界前必须提取 row、entry identity 与文本；返回后重新从当前 table 查找 item，不得继续解引用可能已被刷新删除的 `QTableWidgetItem`。

### 性能与线程验证

- 使用约 20k entries 的 projection fixture 断言 store/read/listener 路径复用不可变 snapshot，显式可变导出仍相互隔离；用复制/冻结调用计数或内存分档证据防止重复深拷贝回归。
- Qt 测试验证首批无需等待滚动即可出现、事件循环推进后自动装填全部、进度单调到完成、无“加载更多”入口、滚动位置不影响调度，以及刷新 generation 会取消旧批次。
- Qt 编辑回归覆盖普通 projection revision 更新不重启 render generation，以及同步 subscriber 强制重建表格后仍不访问已删除 item、译文正确显示。
- 性能断言以算法次数、最终完整性和事件循环可取得处理机会为主；墙钟数据作为基准证据记录，不作为容易受 CI 机器波动影响的唯一正确性条件。

### 低感知自动保存

1. 每个 dirty projection revision 都发出可防抖的内容变更通知；连续编辑必须重启同一个空闲计时器，而不是在首次编辑后的固定时刻打断用户。
2. 自动保存仅在最后一次编辑后连续空闲 10 秒时启动。周期性安全检查也只安排同一空闲防抖，不直接抢占正在进行的编辑。
3. 自动保存继续使用后台 worker，但不得显示 Step2 底部进度条、禁用工作台、闪烁手动保存按钮或显示成功提示；失败仍需向用户报告。
4. 手动保存、项目/版本切换和关闭前保存仍保持显式进度、工作台互斥与完成反馈，不能因静默自动保存而放松数据安全门禁。
5. Qt 定时器测试覆盖连续编辑重置倒计时、clean 状态取消排队保存、静默保存零进度条/零禁用，以及手动保存反馈不回归。
