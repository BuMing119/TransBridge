# Project Session Persistence V2

- **状态**：实现完成，综合 QA 通过（2026-08-18）
- **日期**：2026-08-18
- **需求**：FR19、FR20.3/6、NFR1.3、NFR2.1、NFR3.1、NFR4.1
- **架构**：ADR-018、ADR-006/008 的 2026-08-18 增量
- **问题**：R-021～R-026，以及 R-008 的状态所有权部分
- **依赖**：`platform-contract-foundation-v2` S02～S03；`translation-io-kernel-v2` S01～S02

## 目标与边界

明确 Process、Project、Variant、Session 四种状态作用域，通过 Repository/UnitOfWork、完整 Variant snapshot、replace materialization、两阶段生命周期和 schema migration/quarantine 解决旧译文复活、跨版本串版、会话伪恢复与迟到回调污染。

AppContext 保留为 GUI projection/facade，不再拥有权威业务状态。本 Plan 不实现任务调度，但持久化 owner/task 引用并为 TaskRuntime 提供隔离边界。

## Story 清单

### Story 01：V2 Schema、Repository 与安全迁移框架

[详细设计](stories/story-01-v2-schema-repository-migration.md)

- **目标**：建立带模式版本、类型校验、备份、迁移和隔离的持久化底座。
- **文件落点**：新增 `src/transbridge/domain/project/`、`application/ports/repositories.py`、`persistence/v2/`；保留 `persistence/project.py` facade；`tests/fixtures/persistence/`。
- **实施**：定义 Project/Variant/Session DTO 与 schema version；验证必需字段、类型、引用和路径；迁移前备份；不可迁移数据进入 quarantine 并输出恢复指引；文件名与内部 ID 相互校验。
- **验收**：合法旧数据可确定性迁移；损坏或恶意 ID 不覆盖任意文件；迁移失败不改变原文件；错误可定位。
- **测试**：V1→V2 fixtures、版本前后兼容、路径穿越/ID 欺骗、写入故障和 quarantine 恢复测试。

### Story 02：完整 Variant Snapshot 与 Replace Materialization

[详细设计](stories/story-02-variant-replace-materialization.md)

- **目标**：保存/恢复译文、显式清空、Stage、标签、provenance、revision 和来源身份。
- **文件落点**：`persistence/variant_store.py` facade、新 VariantRepository/aggregate、Collection mutation adapter。
- **实施**：快照保存已知来源命名空间的完整状态；使用字段存在性或 tombstone 区分未提供/清空；加载先恢复 SourceSnapshot 基线，再完整应用目标 Variant；增加 source fingerprint 冲突诊断。
- **验收**：空 Variant 清除旧译文；清空后重启不复活；同 key 多来源隔离；Stage/标签/provenance/revision 往返一致。
- **测试**：已复现旧 bug 的回归、A→空→B 切换、显式清空、fingerprint 变化、10 万条 snapshot 基准。

### Story 03：Project/Variant 两阶段生命周期与 UnitOfWork

[详细设计](stories/story-03-project-lifecycle-uow.md)

- **目标**：让新建、打开、关闭、切换、保存、快照和导出在失败时保持一致。
- **文件落点**：新增 project lifecycle use cases；迁移 MainWindow/workbench project 协调逻辑；repository UoW。
- **实施**：prepare 阶段处理 dirty、保存策略、目标验证和 source materialization；commit 阶段原子更新活动引用与 projection；失败回滚旧上下文；快照加载不得把 current 指针指向快照文件。
- **验收**：任何 prepare/commit 故障后旧项目仍可用；用户取消保存不切换；空/多源项目均可恢复；导出读取一致快照。
- **测试**：每个生命周期节点 fault injection、取消、崩溃恢复、活动引用原子性和非 ASCII 路径集成测试。

### Story 04：Session Aggregate、完整恢复与 Owner 隔离

[详细设计](stories/story-04-session-aggregate-owner.md)

- **目标**：同时恢复 UI 对话、后端推理历史、项目/版本和任务引用，并隔离跨会话事件。
- **文件落点**：迁移 `smart_assistant/session_manager.py`、`session_controller.py`；新增 SessionRepository/Aggregate/use cases；UI session adapter。
- **实施**：Session 保存消息、推理历史/摘要、controller state、active project/variant、pending approvals 和 Task refs；非法 transition 返回 domain error，不用 assert；owner/run_id 校验迟到回调。
- **验收**：恢复后 UI 与 backend history 一致；不可恢复部分明确 degraded；切换先保存再激活；旧 Session 回调不能修改新 Session；`python -O` 行为不变。
- **测试**：重启恢复、切换失败回滚、迟到回调、pending approval、优化模式状态转换和 500 轮生命周期稳定性。

### Story 05：Projection、Dirty 与兼容 facade 迁移

[详细设计](stories/story-05-projection-dirty-facades.md)

- **目标**：消除 AppContext、Step2、VariantStore、Session UI 的重复可写状态。
- **文件落点**：`ui/app_context.py`（或实际对应模块）、Step2/workbench、旧 persistence/session facade、projection tests。
- **实施**：projection 订阅聚合事件；dirty 由聚合修订差异计算；GUI 只提交 commands；旧 getters 保留只读委托；逐调用方迁移并记录删除门禁。
- **验收**：任一业务字段只有一个权威写入点；projection 重建不丢状态；销毁时释放订阅；旧 facade 与新 use case 结果一致。
- **测试**：state ownership 静态/运行断言、projection rebuild、unsubscribe/leak、旧新路径 parity。

## 追溯与历史状态纠偏提议

| 需求/问题 | Story | 历史 Plan 提议状态 |
|---|---|---|
| FR19.1～19.4；R-021～024 | S01～S03 | `project-persistence`: `partially-verified`, V1 overlay/lifecycle `superseded_by` 本 Plan |
| FR19.5～19.7；R-025/026 | S01、S04 | `session-controller`、`session-manager`: `partially-verified`, `blocked_by: project-session-persistence-v2` |
| R-008 | S05 | `ui-workbench`、`agent-tool-expansion`: ownership 部分 `superseded_by` 本 Plan |

## 风险、回退与完成门禁

- 风险：错误迁移破坏用户项目。控制：不可逆动作前备份，迁移在副本完成并验证后替换。
- 风险：新旧 owner 并存。控制：写路径按 use case 逐个切换；测试断言 facade 不持有可写副本。
- 回退：应用版本可回到旧 facade，但 V2 数据保留备份和导出工具；不得静默降级覆盖 V2 文件。
- 完成门禁：历史复现探针全部转为回归；故障注入通过；500 轮 Session 稳定性达到已确认预算。
